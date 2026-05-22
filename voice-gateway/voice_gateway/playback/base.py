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

    async def speak(self, text: str, *, device_id: str, conversation_id: str, turn_id: str) -> PlaybackResource:
        self.events.emit("tts.started", device_id=device_id, conversation_id=conversation_id, turn_id=turn_id)
        started = time.perf_counter()
        resource = await self.tts.synthesize_file(text)
        self.events.emit(
            "tts.completed",
            device_id=device_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            playback_id=resource.playback_id,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

        self.events.emit(
            "playback.started",
            device_id=device_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            playback_id=resource.playback_id,
            url=resource.url,
        )
        ok = await self.device.play_audio_resource(resource)
        if not ok:
            self.events.emit(
                "playback.failed",
                device_id=device_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                playback_id=resource.playback_id,
            )
            raise RuntimeError("device rejected playback resource")
        self.events.emit(
            "playback.finished",
            device_id=device_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            playback_id=resource.playback_id,
        )
        return resource
