from __future__ import annotations

import argparse
import asyncio
import os
import random
import signal
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from voice_gateway.adapters.xiaoai_device import XiaoAIDeviceController
from voice_gateway.app import MinimalLoopGateway
from voice_gateway.asr import ASREngine, SherpaOnnxOfflineASREngine, StaticFinalASREngine
from voice_gateway.audio import EnergyEndpointDetector, SherpaOnnxEndpointDetector
from voice_gateway.config import EndpointingConfig, load_config_from_env
from voice_gateway.dialogue.triggers import contains_wake_word
from voice_gateway.hermes import EchoHermesConnector, OpenAICompatibleHermesConnector
from voice_gateway.models import AudioChunk
from voice_gateway.observability import JsonLineEventLogger
from voice_gateway.playback import EdgeTTSFileEngine, PlaybackManager, StaticTTSEngine

DEFAULT_WAKE_ACK_TEXTS = ("我在", "在", "诶")


class RuntimeState(str, Enum):
    WAIT_WAKE_WORD = "WAIT_WAKE_WORD"
    WAIT_QUESTION = "WAIT_QUESTION"


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
        question_timeout_seconds: float = 8.0,
        ack_suppression_seconds: float = 0.4,
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
        self.ack_suppression_seconds = ack_suppression_seconds
        self.probe = probe
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        self.seq = 0
        self.total_bytes = 0
        self.started_at = 0.0
        self.state = RuntimeState.WAIT_WAKE_WORD
        self.question_deadline: Optional[float] = None
        self.ignore_audio_until = 0.0
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
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.queue.put_nowait(data)

    async def _worker(self) -> None:
        while True:
            data = await self.queue.get()
            try:
                if time.monotonic() < self.ignore_audio_until:
                    continue
                self.seq += 1
                if self.probe:
                    self._log_probe(data)
                chunk = AudioChunk(
                    device_id=self.device_id,
                    seq=self.seq,
                    timestamp_ms=round(time.monotonic() * 1000),
                    pcm=data,
                )
                if self.state == RuntimeState.WAIT_QUESTION and self._question_timed_out():
                    await self.gateway.listen_timeout()
                    await self._enter_wait_wake_word("question_timeout")

                if self.state == RuntimeState.WAIT_WAKE_WORD:
                    await self._handle_wake_chunk(chunk)
                else:
                    result = await self.gateway.accept_audio(chunk)
                    if result is not None:
                        self._drain_queue()
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
        ack_ok = False
        if self.device is not None:
            ack_text = random.choice(self.wake_ack_texts)
            ack_ok = await self.device.play_text(ack_text)
            self.gateway.events.emit(
                "wake_ack.sent",
                device_id=self.device_id,
                wake_word=self.wake_word,
                method="speaker_text_tts",
                text=ack_text,
                ok=ack_ok,
            )
        self._drain_queue()
        # Only suppress microphone audio when the speaker actually played an
        # acknowledgement.  If the ack command failed, users naturally start
        # speaking immediately; dropping the next ~400ms then clips the first
        # character of the real question before ASR sees it.
        self.ignore_audio_until = time.monotonic() + self.ack_suppression_seconds if ack_ok else 0.0
        await self.wake_asr.reset()
        self.wake_endpoint.reset()
        await self.gateway.wakeup()
        self.state = RuntimeState.WAIT_QUESTION
        self.question_deadline = time.monotonic() + self.question_timeout_seconds
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
        self.wake_endpoint.reset()
        await self.wake_asr.reset()
        self.gateway.events.emit(
            "runtime.state_changed",
            device_id=self.device_id,
            to=self.state.value,
            reason=reason,
            wake_word=self.wake_word,
        )

    def _question_timed_out(self) -> bool:
        return self.question_deadline is not None and time.monotonic() >= self.question_deadline

    def _drain_queue(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _log_probe(self, data: bytes) -> None:
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
            print(
                "record stream "
                f"bytes_total={self.total_bytes} chunk={len(data)} elapsed={elapsed:.1f}s "
                f"rms={rms} peak={peak} first16={data[:16].hex(' ')}",
                file=sys.stderr,
            )


async def run_server(args: argparse.Namespace) -> int:
    open_xiaoai_server = _import_open_xiaoai_server(Path(args.xiaozhi_dir))
    config = load_config_from_env()
    events = JsonLineEventLogger()

    wake_asr = _build_asr(args.static_wake_asr_text or args.static_asr_text, config)
    question_asr = _build_asr(args.static_question_asr_text or args.static_asr_text, config)
    wake_endpoint = _build_endpoint(args, config)
    question_endpoint = _build_endpoint(args, config)

    hermes = EchoHermesConnector() if args.echo_hermes else OpenAICompatibleHermesConnector(config.hermes)
    tts = StaticTTSEngine() if args.no_tts else EdgeTTSFileEngine(config.tts)
    device = None if args.no_device_playback else XiaoAIDeviceController(open_xiaoai_server)
    playback = PlaybackManager(tts=tts, device=device, events=events)
    gateway = MinimalLoopGateway(
        device_id=args.device_id,
        asr=question_asr,
        hermes=hermes,
        playback=playback,
        endpoint=question_endpoint,
        events=events,
    )
    runtime = XiaoAIMinimalRuntime(
        gateway,
        device_id=args.device_id,
        wake_asr=wake_asr,
        wake_endpoint=wake_endpoint,
        device=device,
        wake_word=args.wake_word,
        question_timeout_seconds=args.question_timeout,
        ack_suppression_seconds=args.ack_suppression_seconds,
        probe=args.probe,
    )

    loop = asyncio.get_running_loop()
    open_xiaoai_server.register_fn("on_input_data", lambda data: runtime.on_input_data_threadsafe(loop, data))
    open_xiaoai_server.register_fn("on_event", lambda event: None)
    await runtime.start()

    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    print(
        "voice-gateway XiaoAI minimal runtime listening via open_xiaoai_server "
        f"device_id={args.device_id} wake_word={args.wake_word!r}",
        file=sys.stderr,
    )
    # open_xiaoai_server.start_server() is provided by the PyO3 extension and
    # returns an asyncio Future, not a native coroutine.  Use ensure_future so
    # both Future and coroutine implementations are accepted.
    server_task = asyncio.ensure_future(open_xiaoai_server.start_server())
    stop_task = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait({server_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    await runtime.stop()
    for task in done:
        if task is server_task:
            await task
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run voice-gateway minimal loop against a real XiaoAI speaker.")
    parser.add_argument("--xiaozhi-dir", default="../open-xiaoai/examples/xiaozhi")
    parser.add_argument("--device-id", default=os.getenv("VOICE_GATEWAY_DEVICE_ID", "xiaoai-speaker"))
    parser.add_argument("--wake-word", default=os.getenv("VOICE_GATEWAY_WAKE_WORD", "你好"))
    parser.add_argument(
        "--question-timeout",
        type=float,
        default=float(os.getenv("VOICE_GATEWAY_QUESTION_TIMEOUT_SECONDS", "8")),
    )
    parser.add_argument(
        "--ack-suppression-seconds",
        type=float,
        default=float(os.getenv("VOICE_GATEWAY_ACK_SUPPRESSION_SECONDS", "0.4")),
    )
    parser.add_argument("--probe", action="store_true", help="log record stream byte progress")
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
        min_silence_seconds=float(os.getenv("VOICE_GATEWAY_SILERO_MIN_SILENCE", "0.75")),
        min_speech_seconds=float(os.getenv("VOICE_GATEWAY_SILERO_MIN_SPEECH", "0.1")),
        vad_gain_db=float(os.getenv("VOICE_GATEWAY_VAD_GAIN_DB", "24")),
        max_speech_seconds=float(os.getenv("VOICE_GATEWAY_MAX_SPEECH_SECONDS", "8")),
        pre_roll_seconds=float(os.getenv("VOICE_GATEWAY_VAD_PRE_ROLL_SECONDS", "0.8")),
    )


def _import_open_xiaoai_server(xiaozhi_dir: Path):
    path = _abs_path(xiaozhi_dir)
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    import open_xiaoai_server

    return open_xiaoai_server


def _abs_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def main() -> int:
    return asyncio.run(run_server(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
