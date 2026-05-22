from __future__ import annotations

import re
from typing import Protocol

from voice_gateway.models import ASRResult, AudioChunk, AudioWindow


class ASREngine(Protocol):
    async def accept_audio(self, chunk: AudioChunk) -> None:
        ...

    async def transcribe_final(self, window: AudioWindow) -> ASRResult:
        ...

    async def reset(self) -> None:
        ...


class StaticFinalASREngine:
    """Test/development ASR that returns a configured final transcript."""

    def __init__(self, text: str, engine: str = "static") -> None:
        self.text = text
        self.engine = engine
        self.accepted_chunks = 0

    async def accept_audio(self, chunk: AudioChunk) -> None:
        self.accepted_chunks += 1

    async def transcribe_final(self, window: AudioWindow) -> ASRResult:
        return ASRResult(
            text=self.text,
            normalized_text=normalize_text(self.text),
            is_final=True,
            engine=self.engine,
            start_ms=window.start_ms,
            end_ms=window.end_ms,
        )

    async def reset(self) -> None:
        self.accepted_chunks = 0


def normalize_text(text: str) -> str:
    return re.sub(r"[\s，,。！？?：:；;、\"'‘’“”（）()\[\]【】]+", "", text.strip())
