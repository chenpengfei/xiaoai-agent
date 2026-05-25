from __future__ import annotations

import json
import os
import re
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
        console_format: str | None = None,
        console_min_level: str | None = None,
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
        if console_format is None:
            console_format = os.getenv("VOICE_GATEWAY_CONSOLE_FORMAT", "pretty")
        self.console_format = _normalize_console_format(console_format)
        if console_min_level is None:
            console_min_level = os.getenv("VOICE_GATEWAY_CONSOLE_LEVEL") or os.getenv("VOICE_GATEWAY_LOG_LEVEL") or "info"
        self.console_min_level = _normalize_level(console_min_level)
        if event_log_file is None:
            event_log_file = os.getenv("VOICE_GATEWAY_EVENTS_LOG_FILE", "")
        self.event_log_file = Path(event_log_file) if event_log_file else None
        self.service = service
        self.metrics_registry = metrics_registry

    def emit(self, event: str, **fields: Any) -> None:
        if self.suppress_audio_chunks and event in {"audio.chunk.received", "followup.audio_chunk_received"}:
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
        if self.event_log_file is not None:
            self.event_log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.event_log_file.open("a", encoding="utf-8") as f:
                f.write(line)
                f.write("\n")
        if not _level_enabled(str(fields["level"]), self.console_min_level):
            return
        console_line = _format_console_event(item, json_line=line, console_format=self.console_format)
        if console_line:
            print(console_line, file=sys.stderr)


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


def _normalize_console_format(console_format: str) -> str:
    normalized = console_format.strip().lower()
    return normalized if normalized in {"pretty", "json", "none"} else "pretty"


def _format_console_event(item: Event, *, json_line: str, console_format: str) -> str | None:
    if console_format == "none":
        return None
    if console_format == "json":
        return json_line
    return _format_pretty_event(item)


def _format_pretty_event(item: Event) -> str | None:
    fields = item.fields
    event = item.event
    level = _console_level(str(fields.get("level", "info")))
    module, action = _console_module_event(event)
    return _format_pretty_line(
        timestamp_ms=item.timestamp_ms,
        level=level,
        module=module,
        event=action,
        fields=fields,
    )


def runtime_log(module: str, event: str, *, level: str = "info", min_level: str | None = None, **fields: Any) -> None:
    if not runtime_log_enabled(level, min_level=min_level):
        return
    print(
        _format_pretty_line(
            timestamp_ms=_now_ms(),
            level=_console_level(level),
            module=module,
            event=event,
            fields=fields,
        ),
        file=sys.stderr,
    )


def _format_pretty_line(
    *,
    timestamp_ms: int,
    level: str,
    module: str,
    event: str,
    fields: dict[str, Any],
) -> str:
    context = _format_context(fields)
    line = f"{_format_time(timestamp_ms)} {level:<5} {module:<8} {event:<16}"
    return f"{line} {context}".rstrip()


def _format_time(timestamp_ms: int) -> str:
    seconds = timestamp_ms / 1000
    return f"{time.strftime('%H:%M:%S', time.localtime(seconds))}.{timestamp_ms % 1000:03d}"


_CONTEXT_SKIP_KEYS = {"device_id", "level", "service", "span_id", "trace_id"}
_CONTEXT_ALIASES = {
    "conversation_id": "conv",
    "turn_id": "turn",
    "latency_ms": "cost",
    "total_ms": "total",
    "remaining_ms": "remaining",
    "filtered_text": "text",
    "merged_text": "text",
    "user_text": "text",
    "response_text": "text",
}
_CONTEXT_KEY_ORDER = [
    "conversation_id",
    "turn_id",
    "device_id",
    "state",
    "from",
    "to",
    "reason",
    "latency_ms",
    "total_ms",
    "remaining_ms",
    "slowest_stage",
    "model",
    "tokens",
    "text",
    "filtered_text",
    "merged_text",
    "user_text",
    "response_text",
    "ok",
    "retryable",
    "error_type",
    "failure_reason",
    "error",
    "host",
    "port",
    "method",
    "chunks",
    "history_turns",
]
_MODULE_ALIASES = {
    "device": "gateway",
    "hermes": "llm",
    "input_gate": "turn",
    "metrics": "gateway",
    "runtime": "turn",
    "wake_ack": "turn",
    "wake_word": "vad",
    "wakeup": "turn",
}
_SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9_.:/@+-]+$")


def _console_level(level: str) -> str:
    normalized = _normalize_level(level)
    if normalized == "warning":
        return "WARN"
    return normalized.upper()


def _console_module_event(event: str) -> tuple[str, str]:
    parts = event.split(".")
    module = _MODULE_ALIASES.get(parts[0], parts[0])
    action = "_".join(parts[1:]) if len(parts) > 1 else event
    return module, action


def _format_context(fields: dict[str, Any]) -> str:
    ordered_keys = [key for key in _CONTEXT_KEY_ORDER if key in fields]
    ordered_keys.extend(sorted(key for key in fields if key not in _CONTEXT_SKIP_KEYS and key not in ordered_keys))
    pairs: list[str] = []
    used_aliases: set[str] = set()
    for key in ordered_keys:
        if key in _CONTEXT_SKIP_KEYS:
            continue
        value = fields.get(key)
        if value is None:
            continue
        output_key = _CONTEXT_ALIASES.get(key, key)
        if output_key in used_aliases:
            continue
        used_aliases.add(output_key)
        pairs.append(f"{output_key}={_format_context_value(output_key, value)}")
    return " ".join(pairs)


def _format_context_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if key in {"conv", "turn"}:
        return _short_id(str(value))
    if key in {"cost", "total", "remaining"}:
        try:
            return f"{int(value)}ms"
        except (TypeError, ValueError):
            pass
    if isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    elif isinstance(value, list | tuple):
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    else:
        text = str(value)
    if text and _SAFE_VALUE_RE.fullmatch(text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _short_id(value: str) -> str:
    if "_" in value:
        prefix, suffix = value.split("_", 1)
        if suffix:
            return f"{prefix}_{suffix[:6]}"
    return value[:8]


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
