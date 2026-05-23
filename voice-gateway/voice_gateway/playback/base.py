from __future__ import annotations

import asyncio
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol

from voice_gateway.adapters.base import DeviceController, InMemoryDeviceController
from voice_gateway.config import TTSConfig
from voice_gateway.models import PlaybackResource
from voice_gateway.observability.events import EventLogger, JsonLineEventLogger
from voice_gateway.observability.tracing import SpanHandle, TraceManager


class TTSEngine(Protocol):
    async def synthesize_file(self, text: str) -> PlaybackResource:
        ...


class StaticTTSEngine:
    def __init__(self, url: str = "memory://answer.mp3", fmt: str = "mp3") -> None:
        self.url = url
        self.format = fmt
        self.texts: list[str] = []

    async def synthesize_file(self, text: str) -> PlaybackResource:
        self.texts.append(text)
        return PlaybackResource(playback_id=f"p_{uuid.uuid4().hex}", url=self.url, format=self.format)


class EdgeTTSFileEngine:
    def __init__(self, config: TTSConfig = TTSConfig()) -> None:
        self.config = config

    async def synthesize_file(self, text: str) -> PlaybackResource:
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        playback_id = f"p_{uuid.uuid4().hex}"
        name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{playback_id}.mp3"
        path = self.config.output_dir / name
        started = time.perf_counter()
        await asyncio.to_thread(self._run_edge_tts, text, path)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return PlaybackResource(
            playback_id=playback_id,
            url=f"{self.config.http_base_url}/{path.name}",
            format="mp3",
            duration_ms=elapsed_ms,
            local_path=str(path),
        )

    def _run_edge_tts(self, text: str, path: Path) -> None:
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "edge_tts",
                    "--voice",
                    self.config.voice,
                    "--rate",
                    self.config.rate,
                    "--text",
                    text,
                    "--write-media",
                    str(path),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            details = (exc.stderr or exc.stdout or str(exc)).strip()
            raise RuntimeError(f"edge-tts failed with exit code {exc.returncode}: {details}") from exc


class PlaybackManager:
    def __init__(
        self,
        tts: TTSEngine,
        device: Optional[DeviceController] = None,
        events: EventLogger = JsonLineEventLogger(),
    ) -> None:
        self.tts = tts
        self.device = device or InMemoryDeviceController()
        self.events = events

    async def speak(
        self,
        text: str,
        *,
        device_id: str,
        conversation_id: str,
        turn_id: str,
        trace_id: str | None = None,
        timings_ms: Optional[dict[str, int]] = None,
        tracing: TraceManager | None = None,
        parent_span: SpanHandle | None = None,
    ) -> PlaybackResource:
        event_fields = {
            "device_id": device_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "trace_id": trace_id,
        }
        tts_span = tracing.start_child_span("tts", parent_span, event_fields) if tracing is not None else None
        tts_span_id = tts_span.span_id if tts_span is not None else _new_span_id()
        self.events.emit("tts.started", span_id=tts_span_id, **event_fields)
        started = time.perf_counter()
        try:
            resource = await self.tts.synthesize_file(text)
        except Exception as exc:
            if tts_span is not None:
                tts_span.set_error(exc)
                tts_span.end()
            self.events.emit(
                "tts.failed",
                span_id=tts_span_id,
                error_type=type(exc).__name__,
                error=str(exc),
                **event_fields,
            )
            raise
        tts_latency_ms = round((time.perf_counter() - started) * 1000)
        if timings_ms is not None:
            timings_ms["tts"] = tts_latency_ms
        if tts_span is not None:
            tts_span.set_attribute("duration_ms", tts_latency_ms)
            tts_span.set_attribute("playback_id", resource.playback_id)
            tts_span.end()
        self.events.emit(
            "tts.completed",
            span_id=tts_span_id,
            **event_fields,
            playback_id=resource.playback_id,
            latency_ms=tts_latency_ms,
        )

        playback_span = tracing.start_child_span("playback", parent_span, event_fields) if tracing is not None else None
        playback_span_id = playback_span.span_id if playback_span is not None else _new_span_id()
        self.events.emit(
            "playback.started",
            span_id=playback_span_id,
            **event_fields,
            playback_id=resource.playback_id,
            url=resource.url,
        )
        started = time.perf_counter()
        ok = await self.device.play_audio_resource(resource)
        playback_latency_ms = round((time.perf_counter() - started) * 1000)
        if timings_ms is not None:
            timings_ms["playback"] = playback_latency_ms
        if not ok:
            if playback_span is not None:
                playback_span.set_attribute("duration_ms", playback_latency_ms)
                playback_span.set_attribute("playback_id", resource.playback_id)
                playback_span.set_error("device rejected playback resource")
                playback_span.end()
            self.events.emit(
                "playback.failed",
                span_id=playback_span_id,
                **event_fields,
                playback_id=resource.playback_id,
                latency_ms=playback_latency_ms,
            )
            raise RuntimeError("device rejected playback resource")
        if playback_span is not None:
            playback_span.set_attribute("duration_ms", playback_latency_ms)
            playback_span.set_attribute("playback_id", resource.playback_id)
            playback_span.end()
        self.events.emit(
            "playback.finished",
            span_id=playback_span_id,
            **event_fields,
            playback_id=resource.playback_id,
            latency_ms=playback_latency_ms,
        )
        return resource


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]
