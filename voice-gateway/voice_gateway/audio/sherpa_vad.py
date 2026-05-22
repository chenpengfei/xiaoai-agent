from __future__ import annotations

from pathlib import Path
from typing import Optional

from voice_gateway.audio.endpointing import EndpointEvent
from voice_gateway.config import EndpointingConfig
from voice_gateway.models import AudioChunk, AudioWindow


class SherpaOnnxEndpointDetector:
    """sherpa-onnx Silero VAD endpoint detector for real XiaoAI record streams."""

    def __init__(
        self,
        model_path: Path,
        config: EndpointingConfig = EndpointingConfig(),
        *,
        threshold: float = 0.5,
        min_silence_seconds: float = 0.45,
        min_speech_seconds: float = 0.1,
        vad_gain_db: float = 24.0,
        max_speech_seconds: float = 8.0,
        pre_roll_seconds: float = 0.8,
    ) -> None:
        self.model_path = model_path
        self.config = config
        self.threshold = threshold
        self.min_silence_seconds = min_silence_seconds
        self.min_speech_seconds = min_speech_seconds
        self.vad_gain_db = vad_gain_db
        self.max_speech_seconds = max_speech_seconds
        self.pre_roll_seconds = pre_roll_seconds
        self._vad = None
        self._window_size = 0
        self._pending_samples = None
        self._pending_raw_samples = None
        self._sample_cursor = 0
        self._speech_started = False
        self._fallback_pcm = bytearray()
        self._fallback_start_sample = 0
        self._fallback_silence_ms = 0
        self._pre_roll_pcm = bytearray()

    def reset(self) -> None:
        self._vad = None
        self._window_size = 0
        self._pending_samples = None
        self._pending_raw_samples = None
        self._sample_cursor = 0
        self._speech_started = False
        self._fallback_pcm = bytearray()
        self._fallback_start_sample = 0
        self._fallback_silence_ms = 0
        self._pre_roll_pcm = bytearray()

    def accept_chunk(self, chunk: AudioChunk) -> list[EndpointEvent]:
        self._validate_chunk(chunk)
        import numpy as np

        vad = self._ensure_vad()
        raw_samples = np.frombuffer(chunk.pcm, dtype=np.int16)
        samples = raw_samples.astype(np.float32) / 32768.0
        if self.vad_gain_db:
            samples = np.clip(samples * (10 ** (self.vad_gain_db / 20.0)), -1.0, 1.0)
        if self._pending_samples is not None and self._pending_samples.size:
            samples = np.concatenate([self._pending_samples, samples])
        if self._pending_raw_samples is not None and self._pending_raw_samples.size:
            raw_samples = np.concatenate([self._pending_raw_samples, raw_samples])

        usable = (len(samples) // self._window_size) * self._window_size
        self._pending_samples = samples[usable:]
        self._pending_raw_samples = raw_samples[usable:]
        events: list[EndpointEvent] = []

        for offset in range(0, usable, self._window_size):
            frame = samples[offset : offset + self._window_size]
            raw_frame = raw_samples[offset : offset + self._window_size]
            vad.accept_waveform(frame)

            # sherpa-onnx exposes the in-progress utterance via is_speech_detected()
            # / current_segment.  Completed utterances are available via front/pop
            # only after speech has ended.  Do not pop current_segment here, or we
            # can emit a zero-length speech_ended window and feed ASR an empty input.
            if not self._speech_started and vad.is_speech_detected():
                segment = vad.current_segment
                events.append(
                    EndpointEvent(
                        kind="speech_started",
                        timestamp_ms=round(segment.start / self.config.sample_rate * 1000),
                    )
                )
                self._speech_started = True
                pre_roll_samples = len(self._pre_roll_pcm) // 2
                self._fallback_start_sample = max(0, self._sample_cursor - pre_roll_samples)
                self._fallback_pcm = bytearray(self._pre_roll_pcm)
                self._fallback_silence_ms = 0

            if not self._speech_started:
                self._append_pre_roll(raw_frame)

            if self._speech_started:
                self._fallback_pcm.extend(raw_frame.astype("<i2", copy=False).tobytes())
                raw_rms = float(np.sqrt(np.mean(raw_frame.astype(np.float32) ** 2))) if raw_frame.size else 0.0
                frame_ms = round(self._window_size / self.config.sample_rate * 1000)
                if raw_rms <= 20:
                    self._fallback_silence_ms += frame_ms
                else:
                    self._fallback_silence_ms = 0

            if self._speech_started and not vad.is_speech_detected():
                while not vad.empty():
                    vad.pop()
                # Use the raw captured window instead of sherpa's completed
                # segment samples.  The raw window includes our pre-roll and
                # the current trailing frame, which is more forgiving for real
                # far-field XiaoAI audio; model segments can trim the first or
                # last low-energy syllable before final ASR.
                window = self._raw_window(chunk.device_id, self._fallback_start_sample, bytes(self._fallback_pcm))
                if window.duration_ms > 0:
                    events.append(EndpointEvent(kind="speech_ended", window=window, timestamp_ms=window.end_ms))
                self._speech_started = False
                self._fallback_pcm = bytearray()
                self._fallback_silence_ms = 0

            if self._speech_started and self._fallback_silence_ms >= round(self.min_silence_seconds * 1000):
                window = self._raw_window(chunk.device_id, self._fallback_start_sample, bytes(self._fallback_pcm))
                if window.duration_ms > 0:
                    events.append(EndpointEvent(kind="speech_ended", window=window, timestamp_ms=window.end_ms))
                self.reset()
                return events

            self._sample_cursor += self._window_size
        return events

    def flush(self, device_id: str) -> list[EndpointEvent]:
        vad = self._vad
        if vad is None:
            return []
        vad.flush()
        events: list[EndpointEvent] = []
        while not vad.empty():
            segment = vad.front
            vad.pop()
            window = self._segment_to_window(device_id, segment.start, segment.samples)
            events.append(EndpointEvent(kind="speech_ended", window=window, timestamp_ms=window.end_ms))
        return events

    def _ensure_vad(self):
        if self._vad is not None:
            return self._vad
        import numpy as np
        import sherpa_onnx

        if not self.model_path.exists():
            raise FileNotFoundError(f"missing sherpa-onnx VAD model: {self.model_path}")
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(self.model_path)
        cfg.silero_vad.threshold = self.threshold
        cfg.silero_vad.min_silence_duration = self.min_silence_seconds
        cfg.silero_vad.min_speech_duration = self.min_speech_seconds
        cfg.silero_vad.max_speech_duration = self.max_speech_seconds
        cfg.sample_rate = self.config.sample_rate
        cfg.num_threads = 2
        self._vad = sherpa_onnx.VoiceActivityDetector(cfg, 100)
        self._window_size = cfg.silero_vad.window_size
        self._pending_samples = np.empty(0, dtype=np.float32)
        return self._vad

    def _append_pre_roll(self, raw_frame) -> None:
        self._pre_roll_pcm.extend(raw_frame.astype("<i2", copy=False).tobytes())
        max_bytes = round(self.pre_roll_seconds * self.config.sample_rate) * 2
        if max_bytes > 0 and len(self._pre_roll_pcm) > max_bytes:
            del self._pre_roll_pcm[: len(self._pre_roll_pcm) - max_bytes]

    def _raw_window(self, device_id: str, start_sample: int, pcm: bytes) -> AudioWindow:
        start_ms = round(start_sample / self.config.sample_rate * 1000)
        duration_ms = round((len(pcm) // 2) / self.config.sample_rate * 1000)
        return AudioWindow(
            device_id=device_id,
            start_ms=start_ms,
            end_ms=start_ms + duration_ms,
            sample_rate=self.config.sample_rate,
            pcm=pcm,
        )

    def _segment_to_window(self, device_id: str, start_sample: int, samples) -> AudioWindow:
        import numpy as np

        pcm = np.clip(np.array(samples, dtype=np.float32) * 32768.0, -32768, 32767).astype(np.int16).tobytes()
        start_ms = round(start_sample / self.config.sample_rate * 1000)
        duration_ms = round(len(samples) / self.config.sample_rate * 1000)
        return AudioWindow(
            device_id=device_id,
            start_ms=start_ms,
            end_ms=start_ms + duration_ms,
            sample_rate=self.config.sample_rate,
            pcm=pcm,
        )

    def _validate_chunk(self, chunk: AudioChunk) -> None:
        if chunk.sample_rate != self.config.sample_rate:
            raise ValueError(f"expected {self.config.sample_rate} Hz audio, got {chunk.sample_rate}")
        if chunk.channels != 1 or chunk.sample_format != "s16le":
            raise ValueError("minimal loop expects mono s16le PCM")
