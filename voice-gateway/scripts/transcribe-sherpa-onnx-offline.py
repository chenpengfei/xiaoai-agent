#!/usr/bin/env python3
"""Offline transcribe XiaoAI utterance WAVs with sherpa-onnx Paraformer."""

from __future__ import annotations

import json
import os
import re
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sherpa_onnx

VOICE_GATEWAY_DIR = Path(__file__).resolve().parents[1]
ROOT = VOICE_GATEWAY_DIR.parent
MODEL_DIR = Path(
    os.environ.get(
        "SHERPA_ONNX_MODEL_DIR",
        str(ROOT / "models" / "sherpa-onnx-paraformer-zh-2024-03-09"),
    )
)
WAV_DIR = Path(
    os.environ.get("SHERPA_ONNX_WAV_DIR", str(VOICE_GATEWAY_DIR / "audio-samples" / "utterances"))
)
OUT_DIR = Path(
    os.environ.get("SHERPA_ONNX_OUT_DIR", str(VOICE_GATEWAY_DIR / "audio-samples" / "stt-results"))
)


def read_wave(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as f:
        sample_rate = f.getframerate()
        channels = f.getnchannels()
        sample_width = f.getsampwidth()
        frames = f.readframes(f.getnframes())

    if sample_width != 2:
        raise ValueError(f"{path}: expected 16-bit PCM, got sample_width={sample_width}")

    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)

    return sample_rate, samples.astype(np.float32) / 32768.0


def normalize_text(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[\s，,。！？?：:；;、\"'‘’“”（）()\[\]【】]+", "", text)
    return text


def question_from_normalized(text: str) -> str:
    # Current samples were triggered with "小爱同学 ... 你好 ...".  For the
    # Hermes path, use the content after the last "你好" occurrence.
    if "你好" in text:
        return text.rsplit("你好", 1)[1].strip(" ，,。？?：:")
    for prefix in ("小爱同学哎", "小爱同学", "爱同学哎", "爱同学"):
        if text.startswith(prefix):
            return text.removeprefix(prefix).strip(" ，,。？?：:")
    return text


def main() -> int:
    model = MODEL_DIR / "model.int8.onnx"
    tokens = MODEL_DIR / "tokens.txt"
    if not model.exists() or not tokens.exists():
        print(f"missing model files under {MODEL_DIR}", file=sys.stderr)
        return 2

    wavs = sorted(WAV_DIR.glob("*.wav"))
    if not wavs:
        print(f"no wav files found in {WAV_DIR}", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=str(model),
        tokens=str(tokens),
        num_threads=4,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
        provider="cpu",
    )

    all_results = []
    for wav in wavs:
        sample_rate, samples = read_wave(wav)
        if sample_rate != 16000:
            raise ValueError(f"{wav}: expected 16000 Hz, got {sample_rate}")

        stream = recognizer.create_stream()
        started = time.perf_counter()
        stream.accept_waveform(sample_rate, samples)
        recognizer.decode_stream(stream)
        elapsed_ms = round((time.perf_counter() - started) * 1000)

        result = stream.result
        text = getattr(result, "text", str(result)).strip()
        normalized = normalize_text(text)
        question = question_from_normalized(normalized)
        audio_duration_ms = round(len(samples) / sample_rate * 1000)

        payload = {
            "session_id": wav.stem,
            "audio_path": str(wav),
            "text": text,
            "normalized_text": normalized,
            "question": question,
            "language": "zh",
            "audio_duration_ms": audio_duration_ms,
            "duration_ms": elapsed_ms,
            "realtime_factor": round(elapsed_ms / audio_duration_ms, 3) if audio_duration_ms else None,
            "engine": "sherpa-onnx",
            "model": "csukuangfj/sherpa-onnx-paraformer-zh-2024-03-09 model.int8.onnx",
            "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        }

        out = OUT_DIR / f"{wav.stem}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        all_results.append(payload)
        print(json.dumps(payload, ensure_ascii=False))

    summary = OUT_DIR / "summary.json"
    summary.write_text(json.dumps(all_results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"summary={summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
