from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol
from urllib.parse import quote

from server.adapters.base import DeviceController, InMemoryDeviceController
from server.config import TTSConfig
from server.models import PlaybackResource
from server.observability.events import EventLogger, JsonLineEventLogger
from server.observability.tracing import SpanHandle, TraceManager

DEFAULT_CACHED_TTS_TEXTS = ("我在", "在", "诶", "已连接")


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

    async def synthesize_cached_file(self, text: str) -> PlaybackResource:
        return await self.synthesize_file(text)


class EdgeTTSFileEngine:
    engine_name = "edge"
    model_name = "edge-tts"

    def __init__(
        self,
        config: TTSConfig = TTSConfig(),
        *,
        cached_texts: tuple[str, ...] = DEFAULT_CACHED_TTS_TEXTS,
    ) -> None:
        self.config = config
        self.cached_texts = tuple(text for text in cached_texts if text)

    async def synthesize_file(self, text: str) -> PlaybackResource:
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        playback_id = f"p_{uuid.uuid4().hex}"
        path = self._path_for_text(text, playback_id)
        started = time.perf_counter()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(self._run_edge_tts, text, path)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return PlaybackResource(
            playback_id=playback_id,
            url=f"{self.config.http_base_url}/{_relative_url_path(self.config.output_dir, path)}",
            format="mp3",
            duration_ms=elapsed_ms,
            local_path=str(path),
            tts_engine=self.engine_name,
            tts_model=self.config.voice,
        )

    async def synthesize_cached_file(self, text: str) -> PlaybackResource:
        if text not in self.cached_texts:
            raise RuntimeError(f"text is not configured for TTS cache: {text}")
        path = self._cached_path_for_text(text)
        if not path.exists():
            raise RuntimeError(f"cached TTS file is missing for text: {text}")
        playback_id = f"p_{uuid.uuid4().hex}"
        return PlaybackResource(
            playback_id=playback_id,
            url=f"{self.config.http_base_url}/{_relative_url_path(self.config.output_dir, path)}",
            format="mp3",
            duration_ms=0,
            local_path=str(path),
            tts_engine=self.engine_name,
            tts_model=self.config.voice,
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

    async def warm_cache(self) -> None:
        for text in self.cached_texts:
            await self.synthesize_file(text)

    def _path_for_text(self, text: str, playback_id: str) -> Path:
        if text in self.cached_texts:
            return self._cached_path_for_text(text)
        name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{playback_id}.mp3"
        return self.config.output_dir / name

    def _cached_path_for_text(self, text: str) -> Path:
        digest = hashlib.sha256(
            f"{self.config.voice}\n{self.config.rate}\n{text}".encode("utf-8")
        ).hexdigest()[:16]
        return self.config.output_dir / "cache" / f"edge-{digest}.mp3"


def build_tts_engine(config: TTSConfig) -> TTSEngine:
    return EdgeTTSFileEngine(config)


async def warm_tts_engine(engine: TTSEngine) -> None:
    warm_cache = getattr(engine, "warm_cache", None)
    if warm_cache is not None:
        await warm_cache()


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
        return await self._speak(
            text,
            device_id=device_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            trace_id=trace_id,
            timings_ms=timings_ms,
            tracing=tracing,
            parent_span=parent_span,
            require_cached_tts=False,
        )

    async def speak_cached(
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
        return await self._speak(
            text,
            device_id=device_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            trace_id=trace_id,
            timings_ms=timings_ms,
            tracing=tracing,
            parent_span=parent_span,
            require_cached_tts=True,
        )

    async def _speak(
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
        require_cached_tts: bool,
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
            if require_cached_tts:
                synthesize_cached_file = getattr(self.tts, "synthesize_cached_file", None)
                if synthesize_cached_file is None:
                    raise RuntimeError("TTS engine does not support required cache hits")
                resource = await synthesize_cached_file(text)
            else:
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
            if resource.tts_engine:
                tts_span.set_attribute("tts.engine", resource.tts_engine)
            if resource.tts_model:
                tts_span.set_attribute("tts.model", resource.tts_model)
            tts_span.end()
        self.events.emit(
            "tts.completed",
            span_id=tts_span_id,
            **event_fields,
            playback_id=resource.playback_id,
            latency_ms=tts_latency_ms,
            engine=resource.tts_engine,
            model=resource.tts_model,
            format=resource.format,
            text_chars=len(text),
            local_path=resource.local_path,
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


def _relative_url_path(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)
    return quote(relative.as_posix())
