from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from voice_gateway.observability.metrics import DEFAULT_METRICS_REGISTRY, MetricsRegistry


@dataclass(frozen=True)
class Event:
    event: str
    timestamp_ms: int
    fields: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        payload = {"event": self.event, "timestamp_ms": self.timestamp_ms}
        payload.update(self.fields)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class EventLogger(Protocol):
    def emit(self, event: str, **fields: Any) -> None:
        ...


class JsonLineEventLogger:
    def __init__(
        self,
        *,
        suppress_audio_chunks: bool | None = None,
        min_level: str | None = None,
        event_log_file: str | os.PathLike[str] | None = None,
        service: str = "voice-gateway",
        metrics_registry: MetricsRegistry | None = DEFAULT_METRICS_REGISTRY,
    ) -> None:
        if suppress_audio_chunks is None:
            suppress_audio_chunks = os.getenv("VOICE_GATEWAY_SUPPRESS_AUDIO_CHUNKS", "0") not in {"", "0", "false", "False"}
        self.suppress_audio_chunks = suppress_audio_chunks
        if min_level is None:
            min_level = os.getenv("VOICE_GATEWAY_EVENT_LEVEL") or os.getenv("VOICE_GATEWAY_LOG_LEVEL") or "info"
        self.min_level = _normalize_level(min_level)
        if event_log_file is None:
            event_log_file = os.getenv("VOICE_GATEWAY_EVENTS_LOG_FILE", "")
        self.event_log_file = Path(event_log_file) if event_log_file else None
        self.service = service
        self.metrics_registry = metrics_registry

    def emit(self, event: str, **fields: Any) -> None:
        if self.suppress_audio_chunks and event == "audio.chunk.received":
            return
        fields.setdefault("service", self.service)
        fields.setdefault("level", _level_for_event(event))
        fields["level"] = _normalize_level(str(fields["level"]))
        if not _level_enabled(str(fields["level"]), self.min_level):
            return
        item = Event(event=event, timestamp_ms=_now_ms(), fields=fields)
        if self.metrics_registry is not None:
            self.metrics_registry.observe_event(event, fields)
        line = item.to_json()
        print(line, file=sys.stderr)
        if self.event_log_file is not None:
            self.event_log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.event_log_file.open("a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")


class InMemoryEventLogger:
    def __init__(self, *, metrics_registry: MetricsRegistry | None = None) -> None:
        self.events: list[Event] = []
        self.metrics_registry = metrics_registry

    def emit(self, event: str, **fields: Any) -> None:
        fields.setdefault("service", "voice-gateway")
        fields.setdefault("level", _level_for_event(event))
        if self.metrics_registry is not None:
            self.metrics_registry.observe_event(event, fields)
        self.events.append(Event(event=event, timestamp_ms=_now_ms(), fields=fields))

    def names(self) -> list[str]:
        return [event.event for event in self.events]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _level_for_event(event: str) -> str:
    if event.endswith(".failed") or event in {"runtime.worker.failed", "turn.failed"}:
        return "error"
    if event.endswith(".gap") or event.endswith(".silent") or event.endswith(".ignored"):
        return "warning"
    if event == "audio.chunk.received":
        return "debug"
    return "info"


_LEVEL_VALUES = {
    "debug": 10,
    "info": 20,
    "warn": 30,
    "warning": 30,
    "error": 40,
    "critical": 50,
}


def _normalize_level(level: str) -> str:
    normalized = level.strip().lower()
    if normalized == "warn":
        return "warning"
    return normalized if normalized in _LEVEL_VALUES else "info"


def _level_enabled(level: str, min_level: str) -> bool:
    return _LEVEL_VALUES.get(_normalize_level(level), 20) >= _LEVEL_VALUES.get(_normalize_level(min_level), 20)


def runtime_log_enabled(level: str, *, min_level: str | None = None) -> bool:
    if min_level is None:
        min_level = os.getenv("VOICE_GATEWAY_LOG_LEVEL", "info")
    return _level_enabled(level, min_level)
