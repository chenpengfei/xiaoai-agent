from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


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
    def __init__(self, *, suppress_audio_chunks: bool | None = None) -> None:
        if suppress_audio_chunks is None:
            suppress_audio_chunks = os.getenv("VOICE_GATEWAY_SUPPRESS_AUDIO_CHUNKS", "0") not in {"", "0", "false", "False"}
        self.suppress_audio_chunks = suppress_audio_chunks

    def emit(self, event: str, **fields: Any) -> None:
        if self.suppress_audio_chunks and event == "audio.chunk.received":
            return
        item = Event(event=event, timestamp_ms=_now_ms(), fields=fields)
        print(item.to_json(), file=sys.stderr)


class InMemoryEventLogger:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: str, **fields: Any) -> None:
        self.events.append(Event(event=event, timestamp_ms=_now_ms(), fields=fields))

    def names(self) -> list[str]:
        return [event.event for event in self.events]


def _now_ms() -> int:
    return int(time.time() * 1000)
