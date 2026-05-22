from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

from voice_gateway.config import EndpointingConfig
from voice_gateway.models import AudioChunk, AudioWindow


EndpointEventKind = Literal["speech_started", "speech_ended"]


@dataclass(frozen=True)
class EndpointEvent:
    kind: EndpointEventKind
    window: Optional[AudioWindow] = None
    timestamp_ms: Optional[int] = None


class EnergyEndpointDetector:
    """Small s16le energy endpoint detector for the stage-1 minimal loop."""

    def __init__(self, config: EndpointingConfig = EndpointingConfig()) -> None:
        self.config = config
        self._frame_bytes = int(config.sample_rate * config.frame_ms / 1000) * 2
        self.reset()

    def reset(self) -> None:
        self._in_speech = False
        self._candidate_ms = 0
        self._silence_ms = 0
        self._speech_ms = 0
        self._start_ms: Optional[int] = None
        self._candidate_pcm = bytearray()
        self._window_pcm = bytearray()
        self._remainder = bytearray()
        self._remainder_start_ms: Optional[int] = None

    def accept_chunk(self, chunk: AudioChunk) -> list[EndpointEvent]:
        self._validate_chunk(chunk)
        events: list[EndpointEvent] = []
        data_start_ms = self._remainder_start_ms if self._remainder else chunk.timestamp_ms
        data = bytes(self._remainder) + chunk.pcm
        self._remainder.clear()
        self._remainder_start_ms = None

        full_len = len(data) - (len(data) % self._frame_bytes)
        if full_len < len(data):
            self._remainder.extend(data[full_len:])
            self._remainder_start_ms = data_start_ms + (full_len // self._frame_bytes) * self.config.frame_ms

        for offset in range(0, full_len, self._frame_bytes):
            frame = data[offset : offset + self._frame_bytes]
            frame_index = offset // self._frame_bytes
            frame_start_ms = data_start_ms + frame_index * self.config.frame_ms
            event = self._accept_frame(chunk.device_id, frame, frame_start_ms)
            if event is not None:
                events.append(event)
        return events

    def flush(self, device_id: str) -> list[EndpointEvent]:
        if not self._in_speech or not self._window_pcm or self._start_ms is None:
            self.reset()
            return []
        end_ms = self._start_ms + self._speech_ms + self._silence_ms
        window = AudioWindow(
            device_id=device_id,
            start_ms=self._start_ms,
            end_ms=end_ms,
            sample_rate=self.config.sample_rate,
            pcm=bytes(self._window_pcm),
        )
        self.reset()
        return [EndpointEvent(kind="speech_ended", window=window, timestamp_ms=end_ms)]

    def _accept_frame(self, device_id: str, frame: bytes, frame_start_ms: int) -> Optional[EndpointEvent]:
        speech = _rms_s16le(frame) >= self.config.speech_rms_threshold

        if not self._in_speech:
            if speech:
                if self._candidate_ms == 0:
                    self._start_ms = frame_start_ms
                self._candidate_ms += self.config.frame_ms
                self._candidate_pcm.extend(frame)
                if self._candidate_ms >= self.config.min_speech_ms:
                    self._in_speech = True
                    self._speech_ms = self._candidate_ms
                    self._window_pcm = bytearray(self._candidate_pcm)
                    return EndpointEvent(kind="speech_started", timestamp_ms=self._start_ms)
            else:
                self._candidate_ms = 0
                self._candidate_pcm.clear()
                self._start_ms = None
            return None

        self._window_pcm.extend(frame)
        if speech:
            self._speech_ms += self.config.frame_ms + self._silence_ms
            self._silence_ms = 0
        else:
            self._silence_ms += self.config.frame_ms

        if self._speech_ms >= self.config.max_speech_ms or self._silence_ms >= self.config.min_silence_ms:
            end_ms = frame_start_ms + self.config.frame_ms
            window = AudioWindow(
                device_id=device_id,
                start_ms=self._start_ms or frame_start_ms,
                end_ms=end_ms,
                sample_rate=self.config.sample_rate,
                pcm=bytes(self._window_pcm),
            )
            self.reset()
            return EndpointEvent(kind="speech_ended", window=window, timestamp_ms=end_ms)
        return None

    def _validate_chunk(self, chunk: AudioChunk) -> None:
        if chunk.sample_rate != self.config.sample_rate:
            raise ValueError(f"expected {self.config.sample_rate} Hz audio, got {chunk.sample_rate}")
        if chunk.channels != 1 or chunk.sample_format != "s16le":
            raise ValueError("minimal loop expects mono s16le PCM")


def _rms_s16le(frame: bytes) -> int:
    if not frame:
        return 0
    total = 0
    count = len(frame) // 2
    for i in range(0, len(frame) - 1, 2):
        sample = int.from_bytes(frame[i : i + 2], byteorder="little", signed=True)
        total += sample * sample
    return int(math.sqrt(total / max(count, 1)))
