from __future__ import annotations

import argparse
import asyncio
import time
import uuid
import wave
from pathlib import Path
from typing import Callable, Optional

from voice_gateway.asr.base import ASREngine, StaticFinalASREngine
from voice_gateway.audio.endpointing import EnergyEndpointDetector
from voice_gateway.config import load_config_from_env
from voice_gateway.dialogue.state_machine import DialogueStateMachine
from voice_gateway.hermes.base import EchoHermesConnector, HermesConnector
from voice_gateway.hermes.openai_compatible import OpenAICompatibleHermesConnector
from voice_gateway.models import ASRResult, AudioChunk, DialogueState, HermesResponse, HermesTurn, Turn
from voice_gateway.observability.events import EventLogger, JsonLineEventLogger
from voice_gateway.playback.base import EdgeTTSFileEngine, PlaybackManager, StaticTTSEngine


class MinimalLoopGateway:
    """Stage-1 PCM -> endpoint -> final ASR -> Hermes -> resource playback loop."""

    def __init__(
        self,
        *,
        device_id: str,
        asr: ASREngine,
        hermes: HermesConnector,
        playback: PlaybackManager,
        endpoint: Optional[EnergyEndpointDetector] = None,
        events: EventLogger = JsonLineEventLogger(),
        asr_text_transform: Optional[Callable[[ASRResult], Optional[str]]] = None,
    ) -> None:
        self.device_id = device_id
        self.asr = asr
        self.hermes = hermes
        self.playback = playback
        self.endpoint = endpoint or EnergyEndpointDetector()
        self.events = events
        self.dialogue = DialogueStateMachine(events)
        self.asr_text_transform = asr_text_transform
        self.conversation_id: Optional[str] = None
        self.turn_id: Optional[str] = None

    @property
    def state(self) -> DialogueState:
        return self.dialogue.state

    async def wakeup(self) -> None:
        if self.dialogue.state != DialogueState.IDLE:
            self.events.emit("wakeup.ignored", device_id=self.device_id, state=self.dialogue.state.value)
            return
        self.conversation_id = f"c_{uuid.uuid4().hex}"
        self.turn_id = f"t_{uuid.uuid4().hex}"
        self.endpoint.reset()
        await self.asr.reset()
        self.events.emit(
            "wakeup.detected",
            device_id=self.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
        )
        self.dialogue.transition(
            DialogueState.LISTENING,
            reason="wakeup_detected",
            device_id=self.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
        )

    async def accept_audio(self, chunk: AudioChunk) -> Optional[Turn]:
        if self.dialogue.state not in {DialogueState.LISTENING, DialogueState.ENDPOINTING}:
            return None

        self.events.emit(
            "audio.chunk.received",
            device_id=chunk.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            seq=chunk.seq,
            bytes=len(chunk.pcm),
        )
        await self.asr.accept_audio(chunk)

        for event in self.endpoint.accept_chunk(chunk):
            if event.kind == "speech_started" and self.dialogue.state == DialogueState.LISTENING:
                self.events.emit(
                    "vad.speech_started",
                    device_id=chunk.device_id,
                    conversation_id=self.conversation_id,
                    turn_id=self.turn_id,
                    timestamp_ms=event.timestamp_ms,
                )
                self.dialogue.transition(
                    DialogueState.ENDPOINTING,
                    reason="speech_started",
                    device_id=chunk.device_id,
                    conversation_id=self.conversation_id,
                    turn_id=self.turn_id,
                )
            if event.kind == "speech_ended" and event.window is not None:
                self.events.emit(
                    "vad.speech_ended",
                    device_id=chunk.device_id,
                    conversation_id=self.conversation_id,
                    turn_id=self.turn_id,
                    audio_ms=event.window.duration_ms,
                )
                return await self._complete_turn(event.window)
        return None

    async def listen_timeout(self) -> None:
        if self.dialogue.state in {DialogueState.LISTENING, DialogueState.ENDPOINTING}:
            self.endpoint.reset()
            await self.asr.reset()
            self.dialogue.transition(
                DialogueState.IDLE,
                reason="listen_timeout",
                device_id=self.device_id,
                conversation_id=self.conversation_id,
                turn_id=self.turn_id,
            )
            self.conversation_id = None
            self.turn_id = None

    async def recover_to_idle(self, *, reason: str, error: str) -> None:
        self.events.emit(
            "error.recovered",
            device_id=self.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            reason=reason,
            error=error,
            state=self.dialogue.state.value,
        )
        self.endpoint.reset()
        await self.asr.reset()
        if self.dialogue.state != DialogueState.IDLE:
            self.dialogue.transition(
                DialogueState.IDLE,
                reason=reason,
                device_id=self.device_id,
                conversation_id=self.conversation_id,
                turn_id=self.turn_id,
            )
        self.conversation_id = None
        self.turn_id = None

    async def _complete_turn(self, window) -> Turn:
        conversation_id = self.conversation_id or f"c_{uuid.uuid4().hex}"
        turn_id = self.turn_id or f"t_{uuid.uuid4().hex}"
        turn = Turn(
            turn_id=turn_id,
            conversation_id=conversation_id,
            device_id=window.device_id,
            state="captured",
            audio_window=window,
        )

        try:
            self.dialogue.transition(
                DialogueState.THINKING,
                reason="speech_ended",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
            started = time.perf_counter()
            self.events.emit("asr.started", device_id=window.device_id, conversation_id=conversation_id, turn_id=turn_id)
            turn.asr = await self.asr.transcribe_final(window)
            turn.timings_ms["asr"] = round((time.perf_counter() - started) * 1000)
            self.events.emit(
                "asr.completed",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                text=turn.asr.text,
                normalized_text=turn.asr.normalized_text,
                latency_ms=turn.timings_ms["asr"],
            )
            if not turn.asr.normalized_text:
                turn.state = "failed"
                turn.error = "empty_asr"
                self.dialogue.transition(
                    DialogueState.IDLE,
                    reason="asr_empty",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
                return turn

            user_text = turn.asr.normalized_text
            if self.asr_text_transform is not None:
                user_text = self.asr_text_transform(turn.asr)  # type: ignore[arg-type]
            if user_text is None:
                turn.state = "ignored"
                self.events.emit(
                    "asr.ignored",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    text=turn.asr.text,
                    normalized_text=turn.asr.normalized_text,
                )
                return turn
            if not user_text:
                prompt = "你想问什么？"
                self.events.emit(
                    "asr.empty_question",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    text=turn.asr.text,
                    normalized_text=turn.asr.normalized_text,
                )
                self.dialogue.transition(
                    DialogueState.SPEAKING,
                    reason="empty_question_prompt",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
                started = time.perf_counter()
                turn.hermes_response = HermesResponse(text=prompt, should_speak=True, model="local-prompt")
                turn.playback_resource = await self.playback.speak(
                    prompt,
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
                turn.timings_ms["playback"] = round((time.perf_counter() - started) * 1000)
                turn.state = "played"
                return turn

            started = time.perf_counter()
            self.events.emit(
                "hermes.started",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                user_text=user_text,
            )
            turn.hermes_response = await self.hermes.ask(
                HermesTurn(
                    conversation_id=conversation_id,
                    user_text=user_text,
                    speaker=None,
                    history=(),
                )
            )
            turn.timings_ms["hermes"] = round((time.perf_counter() - started) * 1000)
            self.events.emit(
                "hermes.completed",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                latency_ms=turn.timings_ms["hermes"],
                response_text=turn.hermes_response.text,
                should_speak=turn.hermes_response.should_speak,
                model=turn.hermes_response.model,
            )

            if turn.hermes_response.should_speak:
                self.dialogue.transition(
                    DialogueState.SPEAKING,
                    reason="hermes_response_ready",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
                started = time.perf_counter()
                turn.playback_resource = await self.playback.speak(
                    turn.hermes_response.text,
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
                turn.timings_ms["playback"] = round((time.perf_counter() - started) * 1000)
            turn.state = "played"
            return turn
        except Exception as exc:
            turn.state = "failed"
            turn.error = str(exc)
            self.events.emit(
                "error.recovered",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                error=turn.error,
            )
            return turn
        finally:
            if self.dialogue.state == DialogueState.SPEAKING:
                self.dialogue.transition(
                    DialogueState.IDLE,
                    reason="playback_finished",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            elif self.dialogue.state != DialogueState.IDLE:
                self.dialogue.transition(
                    DialogueState.IDLE,
                    reason="turn_finished",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
            self.endpoint.reset()
            await self.asr.reset()
            self.conversation_id = None
            self.turn_id = None


def read_wave_as_chunk(path: Path, *, device_id: str = "offline", seq: int = 1) -> AudioChunk:
    with wave.open(str(path), "rb") as f:
        sample_rate = f.getframerate()
        channels = f.getnchannels()
        sample_width = f.getsampwidth()
        if sample_width != 2:
            raise ValueError(f"{path}: expected 16-bit PCM, got sample_width={sample_width}")
        if channels != 1:
            raise ValueError(f"{path}: expected mono WAV, got channels={channels}")
        pcm = f.readframes(f.getnframes())
    return AudioChunk(device_id=device_id, seq=seq, timestamp_ms=0, sample_rate=sample_rate, pcm=pcm)


async def run_offline_demo(args: argparse.Namespace) -> int:
    config = load_config_from_env()
    events = JsonLineEventLogger()
    asr = StaticFinalASREngine(args.asr_text)
    hermes: HermesConnector = EchoHermesConnector() if args.echo_hermes else OpenAICompatibleHermesConnector(config.hermes)
    tts = StaticTTSEngine() if args.no_tts else EdgeTTSFileEngine(config.tts)
    playback = PlaybackManager(tts=tts, device=None, events=events)
    gateway = MinimalLoopGateway(
        device_id=args.device_id,
        asr=asr,
        hermes=hermes,
        playback=playback,
        endpoint=EnergyEndpointDetector(config.endpointing),
        events=events,
    )
    await gateway.wakeup()
    chunk = read_wave_as_chunk(Path(args.wav), device_id=args.device_id)
    result = await gateway.accept_audio(chunk)
    if result is None:
        for event in gateway.endpoint.flush(args.device_id):
            if event.window is not None:
                result = await gateway._complete_turn(event.window)
                break
    if result is None:
        raise RuntimeError("audio did not produce a speech endpoint")
    print(f"turn={result.state} text={result.asr.text if result.asr else ''} error={result.error or ''}")
    return 0 if result.state != "failed" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the voice-gateway minimal loop against a WAV file.")
    parser.add_argument("--wav", required=True, help="16k mono s16le WAV file")
    parser.add_argument("--asr-text", required=True, help="final ASR text to use while wiring the loop")
    parser.add_argument("--device-id", default="offline-speaker")
    parser.add_argument("--echo-hermes", action="store_true", help="use an in-process echo Hermes connector")
    parser.add_argument("--no-tts", action="store_true", help="skip edge-tts and emit an in-memory playback resource")
    return parser.parse_args()


def main() -> int:
    return asyncio.run(run_offline_demo(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
