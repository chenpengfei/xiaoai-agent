#!/usr/bin/env python3
"""Use sherpa-onnx Silero VAD to cut speech segments from WAV samples."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import sherpa_onnx

VOICE_GATEWAY_DIR = Path(__file__).resolve().parents[1]
ROOT = VOICE_GATEWAY_DIR.parent
IN_DIR = VOICE_GATEWAY_DIR / "audio-samples" / "utterances-gain12"
OUT_DIR = VOICE_GATEWAY_DIR / "audio-samples" / "utterances-vad-gain12"
VAD_MODEL = ROOT / "voice-gateway" / "config" / "silero_vad.onnx"


def read_wave(path: Path) -> tuple[int, np.ndarray]:
    with wave.open(str(path), "rb") as f:
        sr = f.getframerate()
        samples = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    return sr, samples.astype(np.float32) / 32768.0


def write_wave(path: Path, sr: int, samples: np.ndarray) -> None:
    samples_i16 = np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(samples_i16.tobytes())


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(VAD_MODEL)
    cfg.silero_vad.threshold = 0.5
    cfg.silero_vad.min_silence_duration = 0.35
    cfg.silero_vad.min_speech_duration = 0.1
    cfg.silero_vad.max_speech_duration = 10
    cfg.sample_rate = 16000
    cfg.num_threads = 2

    for wav in sorted(IN_DIR.glob("*.wav")):
        sr, samples = read_wave(wav)
        vad = sherpa_onnx.VoiceActivityDetector(cfg, 100)
        ws = cfg.silero_vad.window_size
        segments = []
        for i in range(0, len(samples), ws):
            vad.accept_waveform(samples[i : i + ws])
            while not vad.empty():
                seg = vad.front
                segments.append((seg.start, np.array(seg.samples, dtype=np.float32)))
                vad.pop()
        vad.flush()
        while not vad.empty():
            seg = vad.front
            segments.append((seg.start, np.array(seg.samples, dtype=np.float32)))
            vad.pop()

        if not segments:
            print(f"{wav.name}: no speech")
            continue

        # For current XiaoAI samples, there may be previous playback + wake phrase.
        # Keep all detected speech, but concatenate with 0.2s gap so ASR sees one utterance.
        gap = np.zeros(int(sr * 0.2), dtype=np.float32)
        merged = []
        desc = []
        for start, seg_samples in segments:
            if merged:
                merged.append(gap)
            merged.append(seg_samples)
            desc.append(f"{start/sr:.2f}s+{len(seg_samples)/sr:.2f}s")
        out = OUT_DIR / wav.name
        write_wave(out, sr, np.concatenate(merged))
        print(f"{wav.name}: {len(segments)} segment(s) {', '.join(desc)} -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
