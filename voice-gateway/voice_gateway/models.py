from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class DialogueState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    ENDPOINTING = "ENDPOINTING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"


@dataclass(frozen=True)
class AudioChunk:
    device_id: str
    seq: int
    timestamp_ms: int
    pcm: bytes
    stream_id: str = "record"
    sample_rate: int = 16000
    channels: int = 1
    sample_format: str = "s16le"


@dataclass(frozen=True)
class AudioWindow:
    device_id: str
    start_ms: int
    end_ms: int
    sample_rate: int
    pcm: bytes
    channels: int = 1
    sample_format: str = "s16le"

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(frozen=True)
class ASRResult:
    text: str
    normalized_text: str
    is_final: bool
    engine: str
    language: Optional[str] = None
    confidence: Optional[float] = None
    start_ms: int = 0
    end_ms: int = 0


@dataclass(frozen=True)
class HermesTurn:
    conversation_id: str
    user_text: str
    speaker: None = None
    history: tuple[Any, ...] = ()


@dataclass(frozen=True)
class HermesResponse:
    text: str
    should_speak: bool = True
    model: Optional[str] = None
    latency_ms: Optional[int] = None


@dataclass(frozen=True)
class PlaybackResource:
    playback_id: str
    url: str
    format: str
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    duration_ms: Optional[int] = None
    local_path: Optional[str] = None


@dataclass
class Turn:
    turn_id: str
    conversation_id: str
    device_id: str
    state: str
    audio_window: Optional[AudioWindow] = None
    asr: Optional[ASRResult] = None
    hermes_response: Optional[HermesResponse] = None
    playback_resource: Optional[PlaybackResource] = None
    error: Optional[str] = None
    timings_ms: dict[str, int] = field(default_factory=dict)
