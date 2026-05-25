from __future__ import annotations

import argparse
import asyncio
import time
import uuid
import wave
from pathlib import Path
from typing import Callable, Optional

from server.asr.base import ASREngine, StaticFinalASREngine
from server.audio.endpointing import EnergyEndpointDetector
from server.config import load_config_from_env
from server.dialogue.state_machine import DialogueStateMachine
from server.hermes.base import EchoHermesConnector, HermesConnector
from server.hermes.openai_compatible import OpenAICompatibleHermesConnector
from server.models import ASRResult, AudioChunk, DialogueMessage, DialogueState, HermesResponse, HermesTurn, Turn
from server.observability.events import EventLogger, JsonLineEventLogger, runtime_log
from server.observability.tracing import SpanHandle, TraceManager
from server.playback.base import PlaybackManager, StaticTTSEngine, build_tts_engine, warm_tts_engine


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
        tracing: Optional[TraceManager] = None,
        followup_enabled: bool = False,
        max_history_turns: int = 10,
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
        self.trace_id: Optional[str] = None
        self._root_span_id: Optional[str] = None
        self._root_span: Optional[SpanHandle] = None
        self.tracing = tracing or TraceManager.from_env()
        self.followup_enabled = followup_enabled
        self.max_history_turns = max(0, max_history_turns)
        self.history: list[DialogueMessage] = []

    @property
    def state(self) -> DialogueState:
        return self.dialogue.state

    async def wakeup(self) -> None:
        if self.dialogue.state != DialogueState.IDLE:
            self.events.emit("wakeup.ignored", device_id=self.device_id, state=self.dialogue.state.value)
            return
        self.conversation_id = f"c_{uuid.uuid4().hex}"
        self.history = []
        await self._start_turn(reason="wakeup_detected")

    async def begin_followup_turn(self, *, preserve_endpoint: bool = False) -> None:
        if self.dialogue.state != DialogueState.FOLLOWUP_WAIT:
            self.events.emit("followup.ignored", device_id=self.device_id, state=self.dialogue.state.value)
            return
        if self.conversation_id is None:
            self.conversation_id = f"c_{uuid.uuid4().hex}"
        await self._start_turn(reason="followup_started", reset_endpoint=not preserve_endpoint)
        self.events.emit(
            "followup.started",
            device_id=self.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            trace_id=self.trace_id,
            span_id=self._root_span_id,
            history_turns=self._history_turns(),
        )

    async def followup_timeout(self) -> None:
        if self.dialogue.state in {DialogueState.FOLLOWUP_WAIT, DialogueState.LISTENING, DialogueState.ENDPOINTING}:
            self.events.emit(
                "followup.timeout",
                device_id=self.device_id,
                conversation_id=self.conversation_id,
                turn_id=self.turn_id,
                trace_id=self.trace_id,
                span_id=self._root_span_id,
                history_turns=self._history_turns(),
            )
            self.endpoint.reset()
            await self.asr.reset()
            self.dialogue.transition(
                DialogueState.IDLE,
                reason="followup_timeout",
                device_id=self.device_id,
                conversation_id=self.conversation_id,
                turn_id=self.turn_id,
                trace_id=self.trace_id,
                span_id=self._root_span_id,
            )
            self._end_current_span()
            self._clear_conversation()

    async def _start_turn(self, *, reason: str, reset_endpoint: bool = True) -> None:
        if self.conversation_id is None:
            self.conversation_id = f"c_{uuid.uuid4().hex}"
        self.turn_id = f"t_{uuid.uuid4().hex}"
        self._root_span = self.tracing.start_root_span(
            "voice.turn",
            {
                "device_id": self.device_id,
                "conversation_id": self.conversation_id,
                "turn_id": self.turn_id,
            },
        )
        self.trace_id = self._root_span.trace_id
        self._root_span_id = self._root_span.span_id
        if reset_endpoint:
            self.endpoint.reset()
        await self.asr.reset()
        event_name = "wakeup.detected" if reason == "wakeup_detected" else "followup.turn_started"
        self.events.emit(
            event_name,
            device_id=self.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            trace_id=self.trace_id,
            span_id=self._root_span_id,
            history_turns=self._history_turns(),
        )
        self.dialogue.transition(
            DialogueState.LISTENING,
            reason=reason,
            device_id=self.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            trace_id=self.trace_id,
            span_id=self._root_span_id,
        )

    async def accept_audio(self, chunk: AudioChunk) -> Optional[Turn]:
        return await self._accept_audio(chunk, capture_only=False)

    async def capture_audio(self, chunk: AudioChunk) -> Optional[Turn]:
        return await self._accept_audio(chunk, capture_only=True)

    def note_speech_started(self, chunk: AudioChunk, timestamp_ms: Optional[int]) -> None:
        if self.dialogue.state != DialogueState.LISTENING:
            return
        self.events.emit(
            "vad.speech_started",
            device_id=chunk.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            trace_id=self.trace_id,
            span_id=self._root_span_id,
            timestamp_ms=timestamp_ms,
        )
        self.dialogue.transition(
            DialogueState.ENDPOINTING,
            reason="speech_started",
            device_id=chunk.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            trace_id=self.trace_id,
            span_id=self._root_span_id,
        )

    async def capture_window(self, window) -> Turn:
        self.events.emit(
            "vad.speech_ended",
            device_id=window.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            trace_id=self.trace_id,
            span_id=self._root_span_id,
            audio_ms=window.duration_ms,
        )
        return await self._capture_turn(window)

    async def _accept_audio(self, chunk: AudioChunk, *, capture_only: bool) -> Optional[Turn]:
        if self.dialogue.state not in {DialogueState.LISTENING, DialogueState.ENDPOINTING}:
            return None

        self.events.emit(
            "audio.chunk.received",
            device_id=chunk.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            trace_id=self.trace_id,
            span_id=self._root_span_id,
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
                    trace_id=self.trace_id,
                    span_id=self._root_span_id,
                    timestamp_ms=event.timestamp_ms,
                )
                self.dialogue.transition(
                    DialogueState.ENDPOINTING,
                    reason="speech_started",
                    device_id=chunk.device_id,
                    conversation_id=self.conversation_id,
                    turn_id=self.turn_id,
                    trace_id=self.trace_id,
                    span_id=self._root_span_id,
                )
            if event.kind == "speech_ended" and event.window is not None:
                self.events.emit(
                    "vad.speech_ended",
                    device_id=chunk.device_id,
                    conversation_id=self.conversation_id,
                    turn_id=self.turn_id,
                    trace_id=self.trace_id,
                    span_id=self._root_span_id,
                    audio_ms=event.window.duration_ms,
                )
                if capture_only:
                    return await self._capture_turn(event.window)
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
                trace_id=self.trace_id,
                span_id=self._root_span_id,
            )
            self._clear_conversation()
            if self._root_span is not None:
                self._root_span.end()
            self._root_span = None

    async def recover_to_idle(self, *, reason: str, error: str) -> None:
        self.events.emit(
            "error.recovered",
            device_id=self.device_id,
            conversation_id=self.conversation_id,
            turn_id=self.turn_id,
            trace_id=self.trace_id,
            span_id=self._root_span_id,
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
                trace_id=self.trace_id,
                span_id=self._root_span_id,
            )
        self.conversation_id = None
        self.turn_id = None
        self.trace_id = None
        self._root_span_id = None
        self.history = []
        if self._root_span is not None:
            self._root_span.set_error(error)
            self._root_span.end()
        self._root_span = None

    async def _capture_turn(self, window) -> Turn:
        conversation_id = self.conversation_id or f"c_{uuid.uuid4().hex}"
        turn_id = self.turn_id or f"t_{uuid.uuid4().hex}"
        root_span = self._root_span or self.tracing.start_root_span(
            "voice.turn",
            {
                "device_id": window.device_id,
                "conversation_id": conversation_id,
                "turn_id": turn_id,
            },
        )
        self.conversation_id = conversation_id
        self.turn_id = turn_id
        self._root_span = root_span
        self.trace_id = root_span.trace_id
        self._root_span_id = root_span.span_id
        trace_id = root_span.trace_id
        root_span_id = root_span.span_id
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
                trace_id=trace_id,
                span_id=root_span_id,
            )
            started = time.perf_counter()
            asr_span = self.tracing.start_child_span(
                "asr",
                root_span,
                {
                    "device_id": window.device_id,
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                },
            )
            asr_span_id = asr_span.span_id
            self.events.emit(
                "asr.started",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=asr_span_id,
            )
            try:
                turn.asr = await self.asr.transcribe_final(window)
            except Exception as exc:
                asr_span.set_error(exc)
                asr_span.end()
                raise
            turn.timings_ms["asr"] = round((time.perf_counter() - started) * 1000)
            asr_span.set_attribute("duration_ms", turn.timings_ms["asr"])
            asr_span.set_attribute("asr.engine", turn.asr.engine)
            asr_span.set_attribute("asr.text_length", len(turn.asr.normalized_text or turn.asr.text or ""))
            asr_span.set_attribute("asr.empty", not bool(turn.asr.normalized_text))
            asr_span.end()
            self.events.emit(
                "asr.completed",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=asr_span_id,
                text=turn.asr.text,
                normalized_text=turn.asr.normalized_text,
                latency_ms=turn.timings_ms["asr"],
            )
            if not turn.asr.normalized_text:
                turn.state = "ignored"
                turn.error = "empty_asr"
                root_span.set_attribute("failure_reason", "empty_asr")
                self.events.emit(
                    "asr.ignored",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    span_id=root_span_id,
                    reason="empty_asr",
                    text=turn.asr.text,
                    normalized_text=turn.asr.normalized_text,
                )
                await self._reset_capture_for_more(
                    turn,
                    root_span,
                    reason="asr_empty",
                    failure_reason="empty_asr",
                    last_successful_stage="asr",
                )
                return turn

            turn.user_text = turn.asr.normalized_text
            self.events.emit(
                "turn.captured",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=root_span_id,
                user_text=turn.user_text,
            )
            self.endpoint.reset()
            await self.asr.reset()
            self.dialogue.transition(
                DialogueState.LISTENING,
                reason="merge_wait",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=root_span_id,
            )
            return turn
        except Exception as exc:
            turn.state = "failed"
            turn.error = str(exc)
            root_span.set_error(exc)
            self.events.emit(
                "error.recovered",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=root_span_id,
                error=turn.error,
            )
            await self._finish_captured_terminal_turn(
                turn,
                root_span,
                reason="capture_failed",
                failed_stage="asr",
                failure_reason=type(exc).__name__,
                last_successful_stage=None,
            )
            return turn

    async def _reset_capture_for_more(
        self,
        turn: Turn,
        root_span: SpanHandle,
        *,
        reason: str,
        failure_reason: Optional[str],
        last_successful_stage: Optional[str],
    ) -> None:
        root_span.set_attribute("turn.status", turn.state)
        root_span.set_attribute("failure_reason", failure_reason or turn.error)
        root_span.set_attribute("last_successful_stage", last_successful_stage)
        if self.dialogue.state != DialogueState.LISTENING:
            self.dialogue.transition(
                DialogueState.LISTENING,
                reason=reason,
                device_id=turn.device_id,
                conversation_id=turn.conversation_id,
                turn_id=turn.turn_id,
                trace_id=root_span.trace_id,
                span_id=root_span.span_id,
            )
        self.endpoint.reset()
        await self.asr.reset()

    async def _finish_captured_terminal_turn(
        self,
        turn: Turn,
        root_span: SpanHandle,
        *,
        reason: str,
        failed_stage: Optional[str],
        failure_reason: Optional[str],
        last_successful_stage: Optional[str],
    ) -> None:
        root_span_id = root_span.span_id
        trace_id = root_span.trace_id
        root_span.set_attribute("turn.status", turn.state)
        root_span.set_attribute("failed_stage", failed_stage if turn.state == "failed" else None)
        root_span.set_attribute("failure_reason", failure_reason or turn.error)
        root_span.set_attribute("last_successful_stage", last_successful_stage)
        self._emit_turn_summary(
            turn,
            device_id=turn.device_id,
            conversation_id=turn.conversation_id,
            turn_id=turn.turn_id,
            trace_id=trace_id,
            span_id=root_span_id,
            total_ms=sum(turn.timings_ms.values()),
            failed_stage=failed_stage if turn.state == "failed" else None,
            failure_reason=failure_reason or turn.error,
            last_successful_stage=last_successful_stage,
        )
        if self.dialogue.state != DialogueState.IDLE:
            self.dialogue.transition(
                DialogueState.IDLE,
                reason=reason,
                device_id=turn.device_id,
                conversation_id=turn.conversation_id,
                turn_id=turn.turn_id,
                trace_id=trace_id,
                span_id=root_span_id,
            )
        self.endpoint.reset()
        await self.asr.reset()
        self._clear_conversation()
        root_span.end()
        self._root_span = None

    async def answer_captured_turn(self, turn: Turn) -> Turn:
        conversation_id = turn.conversation_id
        turn_id = turn.turn_id
        root_span = self._root_span or self.tracing.start_root_span(
            "voice.turn",
            {
                "device_id": turn.device_id,
                "conversation_id": conversation_id,
                "turn_id": turn_id,
            },
        )
        root_span_id = root_span.span_id
        trace_id = root_span.trace_id
        turn_started = time.perf_counter()
        failed_stage: Optional[str] = None
        failure_reason: Optional[str] = None
        last_successful_stage: Optional[str] = "asr" if turn.asr is not None else None
        user_text_for_history: Optional[str] = None

        try:
            self.dialogue.transition(
                DialogueState.THINKING,
                reason="merge_window_elapsed",
                device_id=turn.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=root_span_id,
            )
            if not turn.user_text:
                prompt = "你想问什么？"
                self.events.emit(
                    "asr.empty_question",
                    device_id=turn.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    span_id=root_span_id,
                    text=turn.asr.text if turn.asr else "",
                    normalized_text=turn.asr.normalized_text if turn.asr else "",
                )
                self.dialogue.transition(
                    DialogueState.SPEAKING,
                    reason="empty_question_prompt",
                    device_id=turn.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    span_id=root_span_id,
                )
                started = time.perf_counter()
                turn.hermes_response = HermesResponse(text=prompt, should_speak=True, model="local-prompt")
                failed_stage = "tts_playback"
                turn.playback_resource = await self.playback.speak(
                    prompt,
                    device_id=turn.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    timings_ms=turn.timings_ms,
                    tracing=self.tracing,
                    parent_span=root_span,
                )
                turn.timings_ms["tts_playback_total"] = round((time.perf_counter() - started) * 1000)
                turn.state = "played"
                return turn

            started = time.perf_counter()
            hermes_span = self.tracing.start_child_span(
                "hermes",
                root_span,
                {
                    "device_id": turn.device_id,
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "hermes.model": getattr(self.hermes, "model", None),
                },
            )
            hermes_span_id = hermes_span.span_id
            self.events.emit(
                "hermes.started",
                device_id=turn.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=hermes_span_id,
                user_text=turn.user_text,
                history_turns=self._history_turns(),
            )
            failed_stage = "hermes"
            try:
                turn.hermes_response = await self.hermes.ask(
                    HermesTurn(
                        conversation_id=conversation_id,
                        user_text=turn.user_text,
                        speaker=None,
                        history=tuple(self.history),
                    )
                )
            except Exception as exc:
                hermes_span.set_error(exc)
                self._emit_hermes_failed(
                    device_id=turn.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    span_id=hermes_span_id,
                    error=exc,
                    user_text=turn.user_text,
                    history_turns=self._history_turns(),
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
                hermes_span.end()
                raise
            turn.timings_ms["hermes"] = round((time.perf_counter() - started) * 1000)
            hermes_span.set_attribute("duration_ms", turn.timings_ms["hermes"])
            hermes_span.set_attribute("hermes.model", turn.hermes_response.model)
            hermes_span.end()
            last_successful_stage = "hermes"
            self.events.emit(
                "hermes.completed",
                device_id=turn.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=hermes_span_id,
                latency_ms=turn.timings_ms["hermes"],
                response_text=turn.hermes_response.text,
                should_speak=turn.hermes_response.should_speak,
                model=turn.hermes_response.model,
                history_turns=self._history_turns(),
            )
            user_text_for_history = turn.user_text

            if turn.hermes_response.should_speak:
                self.dialogue.transition(
                    DialogueState.SPEAKING,
                    reason="hermes_response_ready",
                    device_id=turn.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    span_id=root_span_id,
                )
                started = time.perf_counter()
                failed_stage = "tts_playback"
                tts_playback_span = self.tracing.start_child_span(
                    "tts_playback",
                    root_span,
                    {
                        "device_id": turn.device_id,
                        "conversation_id": conversation_id,
                        "turn_id": turn_id,
                    },
                )
                try:
                    turn.playback_resource = await self.playback.speak(
                        turn.hermes_response.text,
                        device_id=turn.device_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        trace_id=trace_id,
                        timings_ms=turn.timings_ms,
                        tracing=self.tracing,
                        parent_span=tts_playback_span,
                    )
                except Exception as exc:
                    tts_playback_span.set_error(exc)
                    tts_playback_span.end()
                    raise
                last_successful_stage = "playback"
                turn.timings_ms["tts_playback_total"] = round((time.perf_counter() - started) * 1000)
                tts_playback_span.set_attribute("duration_ms", turn.timings_ms["tts_playback_total"])
                tts_playback_span.set_attribute(
                    "playback_id",
                    turn.playback_resource.playback_id if turn.playback_resource is not None else None,
                )
                tts_playback_span.end()
            turn.state = "played"
            return turn
        except Exception as exc:
            turn.state = "failed"
            turn.error = str(exc)
            failure_reason = type(exc).__name__
            root_span.set_error(exc)
            self.events.emit(
                "error.recovered",
                device_id=turn.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=root_span_id,
                error=turn.error,
            )
            return turn
        finally:
            total_ms = round((time.perf_counter() - turn_started) * 1000)
            root_span.set_attribute("duration_ms", total_ms)
            root_span.set_attribute("turn.status", turn.state)
            root_span.set_attribute("failed_stage", failed_stage if turn.state == "failed" else None)
            root_span.set_attribute("failure_reason", failure_reason or turn.error)
            root_span.set_attribute("last_successful_stage", last_successful_stage)
            if turn.state == "failed":
                root_span.set_error(failure_reason or turn.error or "turn_failed")
            self._emit_turn_summary(
                turn,
                device_id=turn.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=root_span_id,
                total_ms=total_ms,
                failed_stage=failed_stage if turn.state == "failed" else None,
                failure_reason=failure_reason or turn.error,
                last_successful_stage=last_successful_stage,
            )
            if self.dialogue.state == DialogueState.SPEAKING:
                if (
                    self.followup_enabled
                    and turn.state == "played"
                    and turn.error is None
                    and turn.hermes_response is not None
                    and turn.hermes_response.should_speak
                ):
                    if user_text_for_history:
                        self._append_history(user_text_for_history, turn.hermes_response.text)
                    self.dialogue.transition(
                        DialogueState.FOLLOWUP_WAIT,
                        reason="playback_finished",
                        device_id=turn.device_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        trace_id=trace_id,
                        span_id=root_span_id,
                        history_turns=self._history_turns(),
                    )
                else:
                    self.dialogue.transition(
                        DialogueState.IDLE,
                        reason="playback_finished",
                        device_id=turn.device_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        trace_id=trace_id,
                        span_id=root_span_id,
                    )
            elif self.dialogue.state != DialogueState.IDLE:
                self.dialogue.transition(
                    DialogueState.IDLE,
                    reason="turn_finished",
                    device_id=turn.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    span_id=root_span_id,
                )
            self.endpoint.reset()
            await self.asr.reset()
            keep_conversation = self.dialogue.state == DialogueState.FOLLOWUP_WAIT
            if keep_conversation:
                self.turn_id = None
                self.trace_id = None
                self._root_span_id = None
            else:
                self._clear_conversation()
            root_span.end()
            self._root_span = None

    async def _complete_turn(self, window) -> Turn:
        conversation_id = self.conversation_id or f"c_{uuid.uuid4().hex}"
        turn_id = self.turn_id or f"t_{uuid.uuid4().hex}"
        trace_id = self.trace_id or uuid.uuid4().hex
        root_span = self._root_span or self.tracing.start_root_span(
            "voice.turn",
            {
                "device_id": window.device_id,
                "conversation_id": conversation_id,
                "turn_id": turn_id,
            },
        )
        root_span_id = root_span.span_id
        trace_id = root_span.trace_id
        turn_started = time.perf_counter()
        failed_stage: Optional[str] = None
        failure_reason: Optional[str] = None
        last_successful_stage: Optional[str] = None
        user_text_for_history: Optional[str] = None
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
                trace_id=trace_id,
                span_id=root_span_id,
            )
            started = time.perf_counter()
            asr_span = self.tracing.start_child_span(
                "asr",
                root_span,
                {
                    "device_id": window.device_id,
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                },
            )
            asr_span_id = asr_span.span_id
            self.events.emit(
                "asr.started",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=asr_span_id,
            )
            failed_stage = "asr"
            try:
                turn.asr = await self.asr.transcribe_final(window)
            except Exception as exc:
                asr_span.set_error(exc)
                asr_span.end()
                raise
            turn.timings_ms["asr"] = round((time.perf_counter() - started) * 1000)
            asr_span.set_attribute("duration_ms", turn.timings_ms["asr"])
            asr_span.set_attribute("asr.engine", turn.asr.engine)
            asr_span.set_attribute("asr.text_length", len(turn.asr.normalized_text or turn.asr.text or ""))
            asr_span.set_attribute("asr.empty", not bool(turn.asr.normalized_text))
            asr_span.end()
            last_successful_stage = "asr"
            self.events.emit(
                "asr.completed",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=asr_span_id,
                text=turn.asr.text,
                normalized_text=turn.asr.normalized_text,
                latency_ms=turn.timings_ms["asr"],
            )
            if not turn.asr.normalized_text:
                turn.state = "failed"
                turn.error = "empty_asr"
                failure_reason = "empty_asr"
                root_span.set_attribute("failed_stage", "asr")
                root_span.set_attribute("failure_reason", "empty_asr")
                self.dialogue.transition(
                    DialogueState.IDLE,
                    reason="asr_empty",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    span_id=root_span_id,
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
                    trace_id=trace_id,
                    span_id=root_span_id,
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
                    trace_id=trace_id,
                    span_id=root_span_id,
                    text=turn.asr.text,
                    normalized_text=turn.asr.normalized_text,
                )
                self.dialogue.transition(
                    DialogueState.SPEAKING,
                    reason="empty_question_prompt",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    span_id=root_span_id,
                )
                started = time.perf_counter()
                turn.hermes_response = HermesResponse(text=prompt, should_speak=True, model="local-prompt")
                failed_stage = "tts_playback"
                turn.playback_resource = await self.playback.speak(
                    prompt,
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    timings_ms=turn.timings_ms,
                    tracing=self.tracing,
                    parent_span=root_span,
                )
                turn.timings_ms["tts_playback_total"] = round((time.perf_counter() - started) * 1000)
                turn.state = "played"
                return turn

            turn.user_text = user_text
            started = time.perf_counter()
            hermes_span = self.tracing.start_child_span(
                "hermes",
                root_span,
                {
                    "device_id": window.device_id,
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "hermes.model": getattr(self.hermes, "model", None),
                },
            )
            hermes_span_id = hermes_span.span_id
            self.events.emit(
                "hermes.started",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=hermes_span_id,
                user_text=user_text,
                history_turns=self._history_turns(),
            )
            failed_stage = "hermes"
            try:
                turn.hermes_response = await self.hermes.ask(
                    HermesTurn(
                        conversation_id=conversation_id,
                        user_text=user_text,
                        speaker=None,
                        history=tuple(self.history),
                    )
                )
            except Exception as exc:
                hermes_span.set_error(exc)
                self._emit_hermes_failed(
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    span_id=hermes_span_id,
                    error=exc,
                    user_text=user_text,
                    history_turns=self._history_turns(),
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
                hermes_span.end()
                raise
            turn.timings_ms["hermes"] = round((time.perf_counter() - started) * 1000)
            hermes_span.set_attribute("duration_ms", turn.timings_ms["hermes"])
            hermes_span.set_attribute("hermes.model", turn.hermes_response.model)
            hermes_span.end()
            last_successful_stage = "hermes"
            self.events.emit(
                "hermes.completed",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=hermes_span_id,
                latency_ms=turn.timings_ms["hermes"],
                response_text=turn.hermes_response.text,
                should_speak=turn.hermes_response.should_speak,
                model=turn.hermes_response.model,
                history_turns=self._history_turns(),
            )
            user_text_for_history = user_text

            if turn.hermes_response.should_speak:
                self.dialogue.transition(
                    DialogueState.SPEAKING,
                    reason="hermes_response_ready",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    span_id=root_span_id,
                )
                started = time.perf_counter()
                failed_stage = "tts_playback"
                tts_playback_span = self.tracing.start_child_span(
                    "tts_playback",
                    root_span,
                    {
                        "device_id": window.device_id,
                        "conversation_id": conversation_id,
                        "turn_id": turn_id,
                    },
                )
                try:
                    turn.playback_resource = await self.playback.speak(
                        turn.hermes_response.text,
                        device_id=window.device_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        trace_id=trace_id,
                        timings_ms=turn.timings_ms,
                        tracing=self.tracing,
                        parent_span=tts_playback_span,
                    )
                except Exception as exc:
                    tts_playback_span.set_error(exc)
                    tts_playback_span.end()
                    raise
                last_successful_stage = "playback"
                turn.timings_ms["tts_playback_total"] = round((time.perf_counter() - started) * 1000)
                tts_playback_span.set_attribute("duration_ms", turn.timings_ms["tts_playback_total"])
                tts_playback_span.set_attribute(
                    "playback_id",
                    turn.playback_resource.playback_id if turn.playback_resource is not None else None,
                )
                tts_playback_span.end()
            turn.state = "played"
            return turn
        except Exception as exc:
            turn.state = "failed"
            turn.error = str(exc)
            failure_reason = type(exc).__name__
            root_span.set_error(exc)
            self.events.emit(
                "error.recovered",
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=root_span_id,
                error=turn.error,
            )
            return turn
        finally:
            total_ms = round((time.perf_counter() - turn_started) * 1000)
            root_span.set_attribute("duration_ms", total_ms)
            root_span.set_attribute("turn.status", turn.state)
            root_span.set_attribute("failed_stage", failed_stage if turn.state == "failed" else None)
            root_span.set_attribute("failure_reason", failure_reason or turn.error)
            root_span.set_attribute("last_successful_stage", last_successful_stage)
            if turn.state == "failed":
                root_span.set_error(failure_reason or turn.error or "turn_failed")
            self._emit_turn_summary(
                turn,
                device_id=window.device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                trace_id=trace_id,
                span_id=root_span_id,
                total_ms=total_ms,
                failed_stage=failed_stage if turn.state == "failed" else None,
                failure_reason=failure_reason or turn.error,
                last_successful_stage=last_successful_stage,
            )
            if self.dialogue.state == DialogueState.SPEAKING:
                if (
                    self.followup_enabled
                    and turn.state == "played"
                    and turn.error is None
                    and turn.hermes_response is not None
                    and turn.hermes_response.should_speak
                ):
                    if user_text_for_history:
                        self._append_history(user_text_for_history, turn.hermes_response.text)
                    self.dialogue.transition(
                        DialogueState.FOLLOWUP_WAIT,
                        reason="playback_finished",
                        device_id=window.device_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        trace_id=trace_id,
                        span_id=root_span_id,
                        history_turns=self._history_turns(),
                    )
                else:
                    self.dialogue.transition(
                        DialogueState.IDLE,
                        reason="playback_finished",
                        device_id=window.device_id,
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        trace_id=trace_id,
                        span_id=root_span_id,
                    )
            elif self.dialogue.state != DialogueState.IDLE:
                self.dialogue.transition(
                    DialogueState.IDLE,
                    reason="turn_finished",
                    device_id=window.device_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    span_id=root_span_id,
                )
            self.endpoint.reset()
            await self.asr.reset()
            keep_conversation = self.dialogue.state == DialogueState.FOLLOWUP_WAIT
            if keep_conversation:
                self.turn_id = None
                self.trace_id = None
                self._root_span_id = None
            else:
                self._clear_conversation()
            root_span.end()
            self._root_span = None

    def _emit_turn_summary(
        self,
        turn: Turn,
        *,
        device_id: str,
        conversation_id: str,
        turn_id: str,
        trace_id: str,
        span_id: str,
        total_ms: int,
        failed_stage: Optional[str],
        failure_reason: Optional[str],
        last_successful_stage: Optional[str],
    ) -> None:
        stage_ms = dict(turn.timings_ms)
        slowest_stage = _slowest_stage(stage_ms)
        event = "turn.failed" if turn.state == "failed" else "turn.completed"
        self.events.emit(
            event,
            device_id=device_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            trace_id=trace_id,
            span_id=span_id,
            total_ms=total_ms,
            stage_ms=stage_ms,
            slowest_stage=slowest_stage,
            failed_stage=failed_stage,
            failure_reason=failure_reason,
            last_successful_stage=last_successful_stage,
            turn_state=turn.state,
        )

    def _emit_hermes_failed(
        self,
        *,
        device_id: str,
        conversation_id: str,
        turn_id: str,
        trace_id: str,
        span_id: str,
        error: Exception,
        user_text: str,
        history_turns: int,
        latency_ms: int,
    ) -> None:
        self.events.emit(
            "hermes.failed",
            device_id=device_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            trace_id=trace_id,
            span_id=span_id,
            error=str(error),
            error_type=type(error).__name__,
            user_text=user_text,
            history_turns=history_turns,
            latency_ms=latency_ms,
        )

    def _append_history(self, user_text: str, response_text: str) -> None:
        if self.max_history_turns <= 0:
            return
        self.history.extend(
            (
                DialogueMessage(role="user", content=user_text),
                DialogueMessage(role="assistant", content=response_text),
            )
        )
        max_messages = self.max_history_turns * 2
        if len(self.history) > max_messages:
            self.history = self.history[-max_messages:]

    def _history_turns(self) -> int:
        return len(self.history) // 2

    def _end_current_span(self) -> None:
        if self._root_span is not None:
            self._root_span.end()
        self._root_span = None
        self.trace_id = None
        self._root_span_id = None
        self.turn_id = None

    def _clear_conversation(self) -> None:
        self.conversation_id = None
        self.turn_id = None
        self.trace_id = None
        self._root_span_id = None
        self.history = []


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


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]


def _slowest_stage(stage_ms: dict[str, int]) -> Optional[str]:
    if not stage_ms:
        return None
    return max(stage_ms.items(), key=lambda item: item[1])[0]


async def run_offline_demo(args: argparse.Namespace) -> int:
    config = load_config_from_env()
    events = JsonLineEventLogger()
    asr = StaticFinalASREngine(args.asr_text)
    hermes: HermesConnector = EchoHermesConnector() if args.echo_hermes else OpenAICompatibleHermesConnector(config.hermes)
    tts = StaticTTSEngine() if args.no_tts else build_tts_engine(config.tts)
    await warm_tts_engine(tts)
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
    runtime_log(
        "turn",
        "completed" if result.state != "failed" else "failed",
        level="error" if result.state == "failed" else "info",
        state=result.state,
        text=result.asr.text if result.asr else "",
        error=result.error or "",
    )
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
