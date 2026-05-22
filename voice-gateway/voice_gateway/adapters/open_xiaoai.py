from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from voice_gateway.models import AudioChunk, PlaybackResource


@dataclass(frozen=True)
class OpenXiaoAIStream:
    tag: str
    payload: bytes
    device_id: str
    seq: int
    timestamp_ms: int


class OpenXiaoAIAdapter:
    """Protocol adapter for the small subset needed by the minimal loop."""

    def audio_chunk_from_stream(self, stream: OpenXiaoAIStream) -> Optional[AudioChunk]:
        if stream.tag != "record":
            return None
        return AudioChunk(
            device_id=stream.device_id,
            stream_id="record",
            seq=stream.seq,
            timestamp_ms=stream.timestamp_ms,
            sample_rate=16000,
            channels=1,
            sample_format="s16le",
            pcm=stream.payload,
        )

    def is_wakeup_event(self, event: str, data: dict[str, Any]) -> bool:
        if event in {"wakeup", "kws", "recording_started"}:
            return True
        state = str(data.get("state") or data.get("name") or "").lower()
        return state in {"wakeup", "kws", "recording", "recording_started"}

    def play_audio_resource_command(self, resource: PlaybackResource) -> dict[str, Any]:
        return {
            "type": "rpc",
            "method": "start_play",
            "params": {
                "url": resource.url,
                "format": resource.format,
                "playback_id": resource.playback_id,
            },
        }
