from __future__ import annotations

import argparse
import asyncio
import os
import random
import signal
import sys
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Optional

from voice_gateway.adapters.xiaoai_device import XiaoAIDeviceController
from voice_gateway.adapters.xiaoai_ws_server import XiaoAIWebSocketServer
from voice_gateway.app import MinimalLoopGateway
from voice_gateway.asr import ASREngine, SherpaOnnxOfflineASREngine, StaticFinalASREngine
from voice_gateway.audio import EnergyEndpointDetector, SherpaOnnxEndpointDetector
from voice_gateway.config import EndpointingConfig, load_config_from_env
from voice_gateway.dialogue.triggers import contains_wake_word, normalize_trigger_text
from voice_gateway.hermes import EchoHermesConnector, OpenAICompatibleHermesConnector
from voice_gateway.models import AudioChunk, DialogueState
from voice_gateway.observability import JsonLineEventLogger, runtime_log, runtime_log_enabled, start_metrics_server
from voice_gateway.playback import PlaybackManager, StaticTTSEngine, build_tts_engine, warm_tts_engine

DEFAULT_WAKE_ACK_TEXTS = ("我在", "在", "诶")


class RuntimeState(str, Enum):
    WAIT_WAKE_WORD = "WAIT_WAKE_WORD"
    WAIT_QUESTION = "WAIT_QUESTION"
    FOLLOWUP_WAIT = "FOLLOWUP_WAIT"


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
        self.probe = probe
        self.queue: asyncio.Queue[tuple[float, bytes]] = asyncio.Queue(maxsize=500)
        self.seq = 0
        self.total_bytes = 0
        self.started_at = 0.0
        self.state = RuntimeState.WAIT_WAKE_WORD
        self.question_deadline: Optional[float] = None
        self.followup_deadline: Optional[float] = None
        self.ignore_audio_until = 0.0
        self._last_ack_text: Optional[str] = None
        self._ack_filter_ignored_current_question = False
        self._base_asr_text_transform = gateway.asr_text_transform
        self.gateway.asr_text_transform = self._transform_question_asr_text
        self._worker_task: Optional[asyncio.Task] = None

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
            enqueued_at, data = await self.queue.get()
            try:
                if enqueued_at < self.ignore_audio_until:
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
                else:
                    if self.state == RuntimeState.FOLLOWUP_WAIT and self.gateway.state == DialogueState.FOLLOWUP_WAIT:
                        await self.gateway.begin_followup_turn()
                    result = await self.gateway.accept_audio(chunk)
                    if result is not None:
                        if result.state == "ignored" and self._ack_filter_ignored_current_question:
                            self._ack_filter_ignored_current_question = False
                            await self.gateway.wakeup()
                            self.question_deadline = time.monotonic() + self.question_timeout_seconds
                            self.gateway.events.emit(
                                "runtime.question_ignored",
                                device_id=self.device_id,
                                reason="ack_filter_short_text",
                                question_timeout_seconds=self.question_timeout_seconds,
                            )
                            continue
                        self._drain_queue()
                        if self.gateway.state == DialogueState.FOLLOWUP_WAIT:
                            await self._enter_followup_wait("question_finished")
                        else:
                            await self._enter_wait_wake_word("question_finished")
            except Exception as exc:
                self.gateway.events.emit(
                    "runtime.worker.failed",
                    device_id=self.device_id,
                    seq=self.seq,
                    error=str(exc),
                )
                self._drain_queue()
                await self.gateway.recover_to_idle(reason="runtime_worker_exception", error=str(exc))
                await self._enter_wait_wake_word("runtime_worker_exception")

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
        self.ignore_audio_until = (
            time.monotonic() + max(0.0, self.ack_suppression_seconds)
            if ack_ok and self.ack_suppression_seconds > 0
            else 0.0
        )
        if ack_ok:
            self._last_ack_text = ack_text
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

    def _drain_queue(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return

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

    def _transform_question_asr_text(self, asr) -> Optional[str]:
        self._ack_filter_ignored_current_question = False
        if self._base_asr_text_transform is not None:
            user_text = self._base_asr_text_transform(asr)
        else:
            user_text = asr.normalized_text or normalize_trigger_text(asr.text or "")

        if user_text is None:
            return None

        normalized = normalize_trigger_text(user_text)
        if not normalized or self._last_ack_text is None:
            return user_text

        for ack_text in self._ack_filter_candidates():
            normalized_ack = normalize_trigger_text(ack_text)
            if not normalized_ack or not normalized.startswith(normalized_ack):
                continue

            stripped = normalized[len(normalized_ack) :]
            if len(stripped) < self.min_question_text_chars:
                self._ack_filter_ignored_current_question = True
                self.gateway.events.emit(
                    "asr.ack_filter_applied",
                    device_id=self.device_id,
                    action="ignore_short",
                    ack_text=ack_text,
                    text=asr.text,
                    normalized_text=asr.normalized_text,
                    filtered_text=stripped,
                    min_question_text_chars=self.min_question_text_chars,
                )
                return None

            self.gateway.events.emit(
                "asr.ack_filter_applied",
                device_id=self.device_id,
                action="strip_prefix",
                ack_text=ack_text,
                text=asr.text,
                normalized_text=asr.normalized_text,
                filtered_text=stripped,
                min_question_text_chars=self.min_question_text_chars,
            )
            return stripped

        return user_text

    def _ack_filter_candidates(self) -> tuple[str, ...]:
        candidates = []
        if self._last_ack_text:
            candidates.append(self._last_ack_text)
        candidates.extend(self.wake_ack_texts)
        unique = tuple(dict.fromkeys(candidates))
        return tuple(sorted(unique, key=lambda text: len(normalize_trigger_text(text)), reverse=True))

    def _clear_ack_filter(self) -> None:
        self._last_ack_text = None
        self._ack_filter_ignored_current_question = False

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
