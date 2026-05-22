from __future__ import annotations

import asyncio
from pathlib import Path

from voice_gateway.asr.base import normalize_text
from voice_gateway.models import ASRResult, AudioChunk, AudioWindow


class SherpaOnnxOfflineASREngine:
    """Final-only sherpa-onnx Paraformer adapter.

    The dependency is imported lazily so the gateway test loop can run without
    model files installed.
    """

    def __init__(
        self,
        model_dir: Path,
        *,
        num_threads: int = 4,
        provider: str = "cpu",
        engine_name: str = "sherpa-onnx",
    ) -> None:
        self.model_dir = model_dir
        self.num_threads = num_threads
        self.provider = provider
        self.engine_name = engine_name
        self._recognizer = None

    async def accept_audio(self, chunk: AudioChunk) -> None:
        return None

    async def reset(self) -> None:
        return None

    async def transcribe_final(self, window: AudioWindow) -> ASRResult:
        text = await asyncio.to_thread(self._transcribe_sync, window)
        return ASRResult(
            text=text,
            normalized_text=normalize_text(text),
            language="zh",
            is_final=True,
            engine=self.engine_name,
            start_ms=window.start_ms,
            end_ms=window.end_ms,
        )

    def _transcribe_sync(self, window: AudioWindow) -> str:
        import numpy as np
        import sherpa_onnx

        recognizer = self._recognizer
        if recognizer is None:
            model = self.model_dir / "model.int8.onnx"
            tokens = self.model_dir / "tokens.txt"
            if not model.exists() or not tokens.exists():
                raise FileNotFoundError(f"missing sherpa-onnx model files under {self.model_dir}")
            recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=str(model),
                tokens=str(tokens),
                num_threads=self.num_threads,
                sample_rate=window.sample_rate,
                feature_dim=80,
                decoding_method="greedy_search",
                provider=self.provider,
            )
            self._recognizer = recognizer

        samples = np.frombuffer(window.pcm, dtype=np.int16).astype(np.float32) / 32768.0
        stream = recognizer.create_stream()
        stream.accept_waveform(window.sample_rate, samples)
        recognizer.decode_stream(stream)
        return stream.result.text.strip()
