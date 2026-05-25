from __future__ import annotations

import argparse
import asyncio
import os
import random
import signal
import sys
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from voice_gateway.adapters.xiaoai_device import XiaoAIDeviceController
from voice_gateway.adapters.xiaoai_ws_server import XiaoAIWebSocketServer
from voice_gateway.app import MinimalLoopGateway
from voice_gateway.asr import ASREngine, SherpaOnnxOfflineASREngine, StaticFinalASREngine
from voice_gateway.audio import EnergyEndpointDetector, SherpaOnnxEndpointDetector
from voice_gateway.config import EndpointingConfig, load_config_from_env
from voice_gateway.dialogue.triggers import contains_wake_word, normalize_trigger_text
from voice_gateway.hermes import EchoHermesConnector, OpenAICompatibleHermesConnector
from voice_gateway.models import ASRResult, AudioChunk, DialogueState, Turn
from voice_gateway.observability import JsonLineEventLogger, runtime_log, runtime_log_enabled, start_metrics_server
from voice_gateway.playback import PlaybackManager, StaticTTSEngine, build_tts_engine, warm_tts_engine

DEFAULT_WAKE_ACK_TEXTS = ("我在", "在", "诶")


class RuntimeState(str, Enum):
    WAIT_WAKE_WORD = "WAIT_WAKE_WORD"
    WAIT_QUESTION = "WAIT_QUESTION"
    FOLLOWUP_WAIT = "FOLLOWUP_WAIT"


class InputGate:
    def __init__(self, *, post_playback_ignore_seconds: float) -> None:
        self.post_playback_ignore_seconds = max(0.0, post_playback_ignore_seconds)
        self.ignore_audio_until = 0.0
        self.window_id = 0

    def accepts(self, enqueued_at: float) -> bool:
        return enqueued_at >= self.ignore_audio_until

    def suppress_for(self, seconds: float, *, now: Optional[float] = None) -> None:
        seconds = max(0.0, seconds)
        ignore_audio_until = (time.monotonic() if now is None else now) + seconds if seconds > 0 else 0.0
        if ignore_audio_until != self.ignore_audio_until:
            self.window_id += 1
        self.ignore_audio_until = ignore_audio_until

    def suppress_after_playback(self, *, now: Optional[float] = None) -> None:
        self.suppress_for(self.post_playback_ignore_seconds, now=now)

    def clear(self) -> None:
        if self.ignore_audio_until != 0.0:
            self.window_id += 1
        self.ignore_audio_until = 0.0


class TurnAssemblyAction(str, Enum):
    WAITING = "waiting"
    READY = "ready"
    IGNORED = "ignored"


@dataclass(frozen=True)
class TurnAssemblyResult:
    action: TurnAssemblyAction
    turn: Optional[Turn] = None
    reason: Optional[str] = None


class TurnAssembler:
    def __init__(
        self,
        *,
        device_id: str,
        events,
        merge_window_seconds: float,
        wake_ack_texts: tuple[str, ...],
        min_question_text_chars: int,
        base_text_transform: Optional[Callable[[ASRResult], Optional[str]]] = None,
    ) -> None:
        self.device_id = device_id
        self.events = events
        self.merge_window_seconds = max(0.0, merge_window_seconds)
        self.wake_ack_texts = tuple(text for text in wake_ack_texts if text) or DEFAULT_WAKE_ACK_TEXTS
        self.min_question_text_chars = min_question_text_chars
        self.base_text_transform = base_text_transform
        self.pending_turn: Optional[Turn] = None
        self.deadline: Optional[float] = None
        self.last_ack_text: Optional[str] = None

    def set_last_ack_text(self, ack_text: Optional[str]) -> None:
        self.last_ack_text = ack_text

    def clear_ack_filter(self) -> None:
        self.last_ack_text = None

    def clear_pending(self) -> None:
        self.pending_turn = None
        self.deadline = None

    def has_pending(self) -> bool:
        return self.pending_turn is not None and self.deadline is not None

    def timeout_seconds(self, now: float) -> float:
        if self.deadline is None:
            return 0.0
        return max(0.0, self.deadline - now)

    def pop_ready(self) -> Optional[Turn]:
        turn = self.pending_turn
        self.clear_pending()
        return turn

    def add_segment(self, turn: Turn, *, now: Optional[float] = None) -> TurnAssemblyResult:
        user_text = self._text_for_turn(turn)
        if user_text is None:
            return TurnAssemblyResult(TurnAssemblyAction.IGNORED, turn=turn, reason="filtered")
        if not user_text:
            return TurnAssemblyResult(TurnAssemblyAction.IGNORED, turn=turn, reason=turn.error or "empty_text")

        turn.user_text = user_text
        if self.pending_turn is None:
            self.pending_turn = turn
            self.events.emit(
                "runtime.merge_wait_started",
                device_id=self.device_id,
                turn_id=turn.turn_id,
                merge_window_seconds=self.merge_window_seconds,
                user_text=turn.user_text,
            )
        else:
            self._merge_captured_turns(self.pending_turn, turn)
            self.events.emit(
                "runtime.question_merged",
                device_id=self.device_id,
                turn_id=self.pending_turn.turn_id,
                merged_text=self.pending_turn.user_text,
                merge_window_seconds=self.merge_window_seconds,
            )

        if self.merge_window_seconds <= 0:
            ready = self.pop_ready()
            return TurnAssemblyResult(TurnAssemblyAction.READY, turn=ready)

        self.deadline = (time.monotonic() if now is None else now) + self.merge_window_seconds
        self.events.emit(
            "turn_assembly.waiting",
            device_id=self.device_id,
            turn_id=self.pending_turn.turn_id if self.pending_turn is not None else turn.turn_id,
            merge_window_seconds=self.merge_window_seconds,
        )
        return TurnAssemblyResult(TurnAssemblyAction.WAITING, turn=self.pending_turn)

    def _text_for_turn(self, turn: Turn) -> Optional[str]:
        if turn.state != "captured" or turn.asr is None:
            return None
        if self.base_text_transform is not None:
            user_text = self.base_text_transform(turn.asr)
        else:
            user_text = turn.asr.normalized_text or normalize_trigger_text(turn.asr.text or "")
        if user_text is None:
            return None

        normalized = normalize_trigger_text(user_text)
        if not normalized or self.last_ack_text is None:
            return user_text

        for ack_text in self._ack_filter_candidates():
            normalized_ack = normalize_trigger_text(ack_text)
            if not normalized_ack or not normalized.startswith(normalized_ack):
                continue

            stripped = normalized[len(normalized_ack) :]
            if len(stripped) < self.min_question_text_chars:
                self.events.emit(
                    "asr.ack_filter_applied",
                    device_id=self.device_id,
                    action="ignore_short",
                    ack_text=ack_text,
                    text=turn.asr.text,
                    normalized_text=turn.asr.normalized_text,
                    filtered_text=stripped,
                    min_question_text_chars=self.min_question_text_chars,
                )
                return None

            self.events.emit(
                "asr.ack_filter_applied",
                device_id=self.device_id,
                action="strip_prefix",
                ack_text=ack_text,
                text=turn.asr.text,
                normalized_text=turn.asr.normalized_text,
                filtered_text=stripped,
                min_question_text_chars=self.min_question_text_chars,
            )
            return stripped

        return user_text

    def _ack_filter_candidates(self) -> tuple[str, ...]:
        candidates = []
        if self.last_ack_text:
            candidates.append(self.last_ack_text)
        candidates.extend(self.wake_ack_texts)
        unique = tuple(dict.fromkeys(candidates))
        return tuple(sorted(unique, key=lambda text: len(normalize_trigger_text(text)), reverse=True))

    @staticmethod
    def _merge_captured_turns(base: Turn, extra: Turn) -> None:
        base.user_text = f"{base.user_text}{extra.user_text}"
        for key, value in extra.timings_ms.items():
            base.timings_ms[key] = base.timings_ms.get(key, 0) + value


class XiaoAIMinimalRuntime:
    def __init__(
        self,
        gateway: MinimalLoopGateway,
        *,
        device_id: str,
        wake_asr: ASREngine,
        wake_endpoint,
        device: Optional[XiaoAIDeviceController] = None,
        wake_word: str = "你好",
        wake_ack_texts: tuple[str, ...] = DEFAULT_WAKE_ACK_TEXTS,
        question_timeout_seconds: float = 5.0,
        followup_timeout_seconds: float = 15.0,
        ack_suppression_seconds: float = 0.0,
        min_question_text_chars: int = 2,
        merge_window_seconds: float = 0.8,
        post_playback_ignore_seconds: float = 0.6,
        probe: bool = False,
    ) -> None:
        self.gateway = gateway
        self.device_id = device_id
        self.wake_asr = wake_asr
        self.wake_endpoint = wake_endpoint
        self.device = device
        self.wake_word = wake_word
        self.wake_ack_texts = tuple(text for text in wake_ack_texts if text) or DEFAULT_WAKE_ACK_TEXTS
        self.question_timeout_seconds = question_timeout_seconds
        self.followup_timeout_seconds = followup_timeout_seconds
        self.ack_suppression_seconds = ack_suppression_seconds
        self.min_question_text_chars = min_question_text_chars
        self.merge_window_seconds = max(0.0, merge_window_seconds)
        self.post_playback_ignore_seconds = max(0.0, post_playback_ignore_seconds)
        self.input_gate = InputGate(post_playback_ignore_seconds=self.post_playback_ignore_seconds)
        self.turn_assembler = TurnAssembler(
            device_id=device_id,
            events=gateway.events,
            merge_window_seconds=self.merge_window_seconds,
            wake_ack_texts=self.wake_ack_texts,
            min_question_text_chars=self.min_question_text_chars,
            base_text_transform=gateway.asr_text_transform,
        )
        self.probe = probe
        self.queue: asyncio.Queue[tuple[float, bytes]] = asyncio.Queue(maxsize=500)
        self.seq = 0
        self.total_bytes = 0
        self.started_at = 0.0
        self.state = RuntimeState.WAIT_WAKE_WORD
        self.question_deadline: Optional[float] = None
        self.followup_deadline: Optional[float] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._last_reported_input_gate_window_id: Optional[int] = None

    @property
    def ignore_audio_until(self) -> float:
        return self.input_gate.ignore_audio_until

    @ignore_audio_until.setter
    def ignore_audio_until(self, value: float) -> None:
        if value != self.input_gate.ignore_audio_until:
            self.input_gate.window_id += 1
        self.input_gate.ignore_audio_until = value

    def on_input_data_threadsafe(self, loop: asyncio.AbstractEventLoop, data: bytes) -> None:
        if not data:
            return
        loop.call_soon_threadsafe(self._put_nowait, bytes(data))

    async def start(self) -> None:
        await self._enter_wait_wake_word("runtime_started")
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    def _put_nowait(self, data: bytes) -> None:
        item = (time.monotonic(), data)
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.queue.put_nowait(item)

    async def _worker(self) -> None:
        while True:
            if self.turn_assembler.has_pending():
                try:
                    enqueued_at, data = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=self.turn_assembler.timeout_seconds(time.monotonic()),
                    )
                except asyncio.TimeoutError:
                    ready_turn = self.turn_assembler.pop_ready()
                    if ready_turn is not None:
                        await self._finalize_question_turn(ready_turn)
                    continue
            else:
                enqueued_at, data = await self.queue.get()
            try:
                if not self.input_gate.accepts(enqueued_at):
                    self._emit_input_gate_ignored_once(enqueued_at)
                    continue
                self.seq += 1
                if self.probe:
                    self._log_probe(data)
                chunk = AudioChunk(
                    device_id=self.device_id,
                    seq=self.seq,
                    timestamp_ms=round(enqueued_at * 1000),
                    pcm=data,
                )
                if self.state == RuntimeState.WAIT_QUESTION and self._question_timed_out():
                    await self.gateway.listen_timeout()
                    await self._enter_wait_wake_word("question_timeout")
                if self.state == RuntimeState.FOLLOWUP_WAIT and self._followup_timed_out():
                    await self.gateway.followup_timeout()
                    await self._enter_wait_wake_word("followup_timeout")

                if self.state == RuntimeState.WAIT_WAKE_WORD:
                    await self._handle_wake_chunk(chunk)
                elif self.state == RuntimeState.FOLLOWUP_WAIT:
                    await self._handle_followup_wait_chunk(chunk)
                else:
                    result = await self.gateway.capture_audio(chunk)
                    if result is not None:
                        await self._handle_captured_segment(result)
            except Exception as exc:
                self.gateway.events.emit(
                    "runtime.worker.failed",
                    device_id=self.device_id,
                    seq=self.seq,
                    error=str(exc),
                )
                self.turn_assembler.clear_pending()
                self._drain_queue()
                await self.gateway.recover_to_idle(reason="runtime_worker_exception", error=str(exc))
                await self._enter_wait_wake_word("runtime_worker_exception")

    async def _handle_followup_wait_chunk(self, chunk: AudioChunk) -> None:
        if self.gateway.state != DialogueState.FOLLOWUP_WAIT:
            return
        events = list(self.gateway.endpoint.accept_chunk(chunk))
        if not any(event.kind == "speech_started" for event in events):
            return

        followup_started = False
        for event in events:
            if event.kind == "speech_started":
                await self.gateway.begin_followup_turn(preserve_endpoint=True)
                self.gateway.note_speech_started(chunk, event.timestamp_ms)
                self.state = RuntimeState.WAIT_QUESTION
                self.question_deadline = time.monotonic() + self.question_timeout_seconds
                self.followup_deadline = None
                followup_started = True
                self.gateway.events.emit(
                    "runtime.state_changed",
                    device_id=self.device_id,
                    to=self.state.value,
                    reason="followup_speech_started",
                    question_timeout_seconds=self.question_timeout_seconds,
                )
            if event.kind == "speech_ended" and event.window is not None and followup_started:
                result = await self.gateway.capture_window(event.window)
                await self._handle_captured_segment(result)

    async def _handle_captured_segment(self, result: Turn) -> None:
        assembly = self.turn_assembler.add_segment(result)
        if assembly.action == TurnAssemblyAction.IGNORED:
            self.question_deadline = time.monotonic() + self.question_timeout_seconds
            self.gateway.events.emit(
                "runtime.question_ignored",
                device_id=self.device_id,
                reason=assembly.reason or result.error or result.state,
                question_timeout_seconds=self.question_timeout_seconds,
            )
            return
        if assembly.action == TurnAssemblyAction.READY and assembly.turn is not None:
            self.question_deadline = None
            await self._finalize_question_turn(assembly.turn)

    async def _finalize_question_turn(self, turn: Turn) -> None:
        if turn.state == "captured":
            await self.gateway.answer_captured_turn(turn)
        if self.gateway.state == DialogueState.FOLLOWUP_WAIT:
            drained = self._drain_queue()
            if self.post_playback_ignore_seconds > 0:
                self.input_gate.suppress_after_playback()
            if drained:
                self.gateway.events.emit(
                    "runtime.playback_backlog_drained",
                    device_id=self.device_id,
                    reason="before_followup_wait",
                    chunks=drained,
                    post_playback_ignore_seconds=self.post_playback_ignore_seconds,
                )
            await self._enter_followup_wait("question_finished")
        else:
            await self._enter_wait_wake_word("question_finished")

    async def _handle_wake_chunk(self, chunk: AudioChunk) -> None:
        for event in self.wake_endpoint.accept_chunk(chunk):
            if event.kind == "speech_started":
                self.gateway.events.emit(
                    "wake_word.speech_started",
                    device_id=chunk.device_id,
                    timestamp_ms=event.timestamp_ms,
                )
            if event.kind == "speech_ended" and event.window is not None:
                self.gateway.events.emit(
                    "wake_word.speech_ended",
                    device_id=chunk.device_id,
                    audio_ms=event.window.duration_ms,
                )
                asr = await self.wake_asr.transcribe_final(event.window)
                self.gateway.events.emit(
                    "wake_word.asr_completed",
                    device_id=chunk.device_id,
                    text=asr.text,
                    normalized_text=asr.normalized_text,
                )
                if contains_wake_word(asr.text or asr.normalized_text, self.wake_word):
                    await self._handle_wake_detected(asr.text or asr.normalized_text)
                    return
                self.gateway.events.emit(
                    "wake_word.ignored",
                    device_id=chunk.device_id,
                    wake_word=self.wake_word,
                    text=asr.text,
                    normalized_text=asr.normalized_text,
                )
                self.wake_endpoint.reset()
                await self.wake_asr.reset()

    async def _handle_wake_detected(self, text: str) -> None:
        self.gateway.events.emit(
            "wake_word.detected",
            device_id=self.device_id,
            wake_word=self.wake_word,
            text=text,
        )
        ack_started_at = time.monotonic()
        self._drain_queue_before(ack_started_at)
        ack_ok = False
        if self.device is not None:
            ack_text = random.choice(self.wake_ack_texts)
            error = None
            try:
                ack_id = f"wake_ack_{uuid.uuid4().hex}"
                await self.gateway.playback.speak_cached(
                    ack_text,
                    device_id=self.device_id,
                    conversation_id=ack_id,
                    turn_id=ack_id,
                )
                ack_ok = True
            except Exception as exc:
                error = str(exc)
            self.gateway.events.emit(
                "wake_ack.sent",
                device_id=self.device_id,
                wake_word=self.wake_word,
                method="edge_tts_url",
                text=ack_text,
                ok=ack_ok,
                error=error,
            )
        # Only suppress microphone audio when explicitly configured.  The
        # default is no extra suppression because users may start speaking
        # during the acknowledgement and we need to preserve those first words.
        if ack_ok and self.ack_suppression_seconds > 0:
            self.input_gate.suppress_for(self.ack_suppression_seconds)
        else:
            self.input_gate.clear()
        if ack_ok:
            self.turn_assembler.set_last_ack_text(ack_text)
        else:
            self._clear_ack_filter()
        await self.wake_asr.reset()
        self.wake_endpoint.reset()
        await self.gateway.wakeup()
        self.state = RuntimeState.WAIT_QUESTION
        self.question_deadline = time.monotonic() + self.question_timeout_seconds
        self.followup_deadline = None
        self.gateway.events.emit(
            "runtime.state_changed",
            device_id=self.device_id,
            to=self.state.value,
            reason="wake_word_detected",
            question_timeout_seconds=self.question_timeout_seconds,
        )

    async def _enter_wait_wake_word(self, reason: str) -> None:
        if self.gateway.state.value != "IDLE":
            await self.gateway.recover_to_idle(reason=reason, error="")
        self.state = RuntimeState.WAIT_WAKE_WORD
        self.question_deadline = None
        self.followup_deadline = None
        self.turn_assembler.clear_pending()
        self._clear_ack_filter()
        self.wake_endpoint.reset()
        await self.wake_asr.reset()
        self.gateway.events.emit(
            "runtime.state_changed",
            device_id=self.device_id,
            to=self.state.value,
            reason=reason,
            wake_word=self.wake_word,
        )

    async def _enter_followup_wait(self, reason: str) -> None:
        self.state = RuntimeState.FOLLOWUP_WAIT
        self.question_deadline = None
        self.followup_deadline = time.monotonic() + self.followup_timeout_seconds
        self.turn_assembler.clear_pending()
        self._clear_ack_filter()
        self.gateway.events.emit(
            "runtime.state_changed",
            device_id=self.device_id,
            to=self.state.value,
            reason=reason,
            followup_timeout_seconds=self.followup_timeout_seconds,
        )

    def _question_timed_out(self) -> bool:
        return self.question_deadline is not None and time.monotonic() >= self.question_deadline

    def _followup_timed_out(self) -> bool:
        return self.followup_deadline is not None and time.monotonic() >= self.followup_deadline

    def _drain_queue(self) -> int:
        drained = 0
        while True:
            try:
                self.queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                return drained

    def _drain_queue_before(self, cutoff: float) -> None:
        kept: list[tuple[float, bytes]] = []
        while True:
            try:
                item = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item[0] >= cutoff:
                kept.append(item)
        for item in kept:
            self.queue.put_nowait(item)

    def _clear_ack_filter(self) -> None:
        self.turn_assembler.clear_ack_filter()

    def _emit_input_gate_ignored_once(self, enqueued_at: float) -> None:
        if self._last_reported_input_gate_window_id == self.input_gate.window_id:
            return
        self._last_reported_input_gate_window_id = self.input_gate.window_id
        self.gateway.events.emit(
            "input_gate.ignored",
            device_id=self.device_id,
            reason="ignore_window",
            state=self.state.value,
            remaining_ms=round(max(0.0, self.input_gate.ignore_audio_until - enqueued_at) * 1000),
        )

    def _log_probe(self, data: bytes) -> None:
        probe_min_level = os.getenv("VOICE_GATEWAY_AUDIO_PROBE_LEVEL", "warning")
        if not runtime_log_enabled("info") or not runtime_log_enabled("info", min_level=probe_min_level):
            return
        if self.started_at == 0.0:
            self.started_at = time.monotonic()
        self.total_bytes += len(data)
        interval_bytes = max(1, int(os.getenv("VOICE_GATEWAY_PROBE_INTERVAL_BYTES", "160000")))
        if self.total_bytes % interval_bytes < len(data):
            elapsed = time.monotonic() - self.started_at
            peak = 0
            rms = 0
            try:
                import array
                import math

                values = array.array("h")
                values.frombytes(data)
                if sys.byteorder != "little":
                    values.byteswap()
                if values:
                    peak = max(abs(value) for value in values)
                    rms = round(math.sqrt(sum(value * value for value in values) / len(values)))
            except Exception:
                pass
            runtime_log(
                "audio",
                "stream_probe",
                min_level=probe_min_level,
                bytes_total=self.total_bytes,
                chunk=len(data),
                elapsed=f"{elapsed:.1f}s",
                rms=rms,
                peak=peak,
                first16=data[:16].hex(" "),
            )


async def run_server(args: argparse.Namespace) -> int:
    xiaoai_server = XiaoAIWebSocketServer(host=args.host, port=args.port)
    config = load_config_from_env()
    events = JsonLineEventLogger()
    metrics_server = None
    if args.metrics:
        metrics_server = start_metrics_server(host=args.metrics_host, port=args.metrics_port)
        events.emit(
            "metrics.server.started",
            host=args.metrics_host,
            port=args.metrics_port,
        )

    wake_asr = _build_asr(args.static_wake_asr_text or args.static_asr_text, config)
    question_asr = _build_asr(args.static_question_asr_text or args.static_asr_text, config)
    wake_endpoint = _build_endpoint(args, config)
    question_endpoint = _build_endpoint(args, config)

    hermes = EchoHermesConnector() if args.echo_hermes else OpenAICompatibleHermesConnector(config.hermes)
    tts = StaticTTSEngine() if args.no_tts else build_tts_engine(config.tts)
    await warm_tts_engine(tts)
    device = None if args.no_device_playback else XiaoAIDeviceController(xiaoai_server)
    playback = PlaybackManager(tts=tts, device=device, events=events)
    gateway = MinimalLoopGateway(
        device_id=args.device_id,
        asr=question_asr,
        hermes=hermes,
        playback=playback,
        endpoint=question_endpoint,
        events=events,
        followup_enabled=args.followup_timeout > 0,
    )
    runtime = XiaoAIMinimalRuntime(
        gateway,
        device_id=args.device_id,
        wake_asr=wake_asr,
        wake_endpoint=wake_endpoint,
        device=device,
        wake_word=args.wake_word,
        question_timeout_seconds=args.question_timeout,
        followup_timeout_seconds=args.followup_timeout,
        ack_suppression_seconds=args.ack_suppression_seconds,
        min_question_text_chars=args.min_question_text_chars,
        merge_window_seconds=args.merge_window_seconds,
        post_playback_ignore_seconds=args.post_playback_ignore_seconds,
        probe=args.probe,
    )

    loop = asyncio.get_running_loop()
    xiaoai_server.register_fn("on_input_data", lambda data: runtime.on_input_data_threadsafe(loop, data))
    xiaoai_server.register_fn("on_event", lambda event: None)
    if device is not None:
        xiaoai_server.register_fn(
            "on_connected",
            lambda: _play_connected_prompt(gateway, args.device_id),
        )
    await runtime.start()

    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    runtime_log(
        "gateway",
        "started",
        host=args.host,
        port=args.port,
        device_id=args.device_id,
        wake_word=args.wake_word,
    )
    server_task = asyncio.create_task(xiaoai_server.start_server())
    stop_task = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait({server_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await runtime.stop()
    await xiaoai_server.stop()
    for task in done:
        if task is server_task:
            await task
    if metrics_server is not None:
        metrics_server.shutdown()
    return 0


async def _play_connected_prompt(gateway: MinimalLoopGateway, device_id: str) -> None:
    connection_id = f"device_connected_{uuid.uuid4().hex}"
    text = "已连接"
    error = None
    ok = False
    try:
        await gateway.playback.speak_cached(
            text,
            device_id=device_id,
            conversation_id=connection_id,
            turn_id=connection_id,
        )
        ok = True
    except Exception as exc:
        error = str(exc)
    gateway.events.emit(
        "device.connected",
        device_id=device_id,
        text=text,
        method="edge_tts_url",
        ok=ok,
        error=error,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run voice-gateway minimal loop against a real XiaoAI speaker.")
    parser.add_argument("--host", default=os.getenv("VOICE_GATEWAY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("VOICE_GATEWAY_PORT", "4399")))
    parser.add_argument("--device-id", default=os.getenv("VOICE_GATEWAY_DEVICE_ID", "xiaoai-speaker"))
    parser.add_argument("--wake-word", default=os.getenv("VOICE_GATEWAY_WAKE_WORD", "你好"))
    parser.add_argument(
        "--question-timeout",
        type=float,
        default=float(os.getenv("VOICE_GATEWAY_QUESTION_TIMEOUT_SECONDS", "5")),
    )
    parser.add_argument(
        "--followup-timeout",
        type=float,
        default=float(os.getenv("VOICE_GATEWAY_FOLLOWUP_TIMEOUT_SECONDS", "15")),
        help="seconds to keep listening for a follow-up after playback; use 0 to disable",
    )
    parser.add_argument(
        "--ack-suppression-seconds",
        type=float,
        default=float(os.getenv("VOICE_GATEWAY_ACK_SUPPRESSION_SECONDS", "0")),
    )
    parser.add_argument(
        "--min-question-text-chars",
        type=int,
        default=int(os.getenv("VOICE_GATEWAY_MIN_QUESTION_TEXT_CHARS", "2")),
    )
    parser.add_argument(
        "--merge-window-seconds",
        type=float,
        default=float(os.getenv("VOICE_GATEWAY_MERGE_WINDOW_SECONDS", "0.8")),
        help="seconds to wait for a continuation after a captured question segment",
    )
    parser.add_argument(
        "--post-playback-ignore-seconds",
        type=float,
        default=float(os.getenv("VOICE_GATEWAY_POST_PLAYBACK_IGNORE_SECONDS", "0.6")),
        help="seconds to ignore microphone audio after answer playback before follow-up listening",
    )
    parser.add_argument("--probe", action="store_true", help="log record stream byte progress")
    parser.add_argument(
        "--metrics",
        action=argparse.BooleanOptionalAction,
        default=os.getenv("VOICE_GATEWAY_METRICS_ENABLED", "1") not in {"", "0", "false", "False"},
        help="serve Prometheus-compatible metrics",
    )
    parser.add_argument("--metrics-host", default=os.getenv("VOICE_GATEWAY_METRICS_HOST", "127.0.0.1"))
    parser.add_argument("--metrics-port", type=int, default=int(os.getenv("VOICE_GATEWAY_METRICS_PORT", "9109")))
    parser.add_argument("--energy-vad", action="store_true", help="use simple energy endpointing instead of Silero VAD")
    parser.add_argument("--static-asr-text", default="", help="dev override; skip real ASR and use this transcript")
    parser.add_argument("--static-wake-asr-text", default="", help="dev override for wake-word ASR only")
    parser.add_argument("--static-question-asr-text", default="", help="dev override for question ASR only")
    parser.add_argument("--echo-hermes", action="store_true", help="dev override; skip Hermes HTTP and echo the question")
    parser.add_argument("--no-tts", action="store_true", help="dev override; skip edge-tts and create memory playback resources")
    parser.add_argument("--no-device-playback", action="store_true", help="dev override; do not call speaker miplayer")
    return parser.parse_args()


def _build_asr(static_text: str, config) -> ASREngine:
    if static_text:
        return StaticFinalASREngine(static_text)
    return SherpaOnnxOfflineASREngine(_abs_path(config.sherpa_model_dir))


def _build_endpoint(args: argparse.Namespace, config):
    if args.energy_vad:
        return EnergyEndpointDetector(config.endpointing)
    return SherpaOnnxEndpointDetector(
        _abs_path(config.silero_vad_model),
        EndpointingConfig(sample_rate=16000),
        threshold=float(os.getenv("VOICE_GATEWAY_SILERO_VAD_THRESHOLD", "0.5")),
        min_silence_seconds=float(os.getenv("VOICE_GATEWAY_SILERO_MIN_SILENCE", "0.5")),
        min_speech_seconds=float(os.getenv("VOICE_GATEWAY_SILERO_MIN_SPEECH", "0.1")),
        vad_gain_db=float(os.getenv("VOICE_GATEWAY_VAD_GAIN_DB", "24")),
        max_speech_seconds=float(os.getenv("VOICE_GATEWAY_MAX_SPEECH_SECONDS", "8")),
        pre_roll_seconds=float(os.getenv("VOICE_GATEWAY_VAD_PRE_ROLL_SECONDS", "0.8")),
    )


def _abs_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def main() -> int:
    return asyncio.run(run_server(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
