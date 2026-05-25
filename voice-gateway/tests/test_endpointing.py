import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from server.audio import EnergyEndpointDetector, SherpaOnnxEndpointDetector
from server.config import EndpointingConfig
from server.models import AudioChunk


def pcm_frame(amplitude: int, frames: int) -> bytes:
    sample = amplitude.to_bytes(2, "little", signed=True)
    return sample * frames


class EnergyEndpointDetectorTest(unittest.TestCase):
    def test_emits_speech_started_and_ended(self):
        config = EndpointingConfig(
            frame_ms=30,
            speech_rms_threshold=450,
            min_speech_ms=90,
            min_silence_ms=90,
        )
        detector = EnergyEndpointDetector(config)
        samples_per_frame = int(config.sample_rate * config.frame_ms / 1000)
        pcm = (
            pcm_frame(0, samples_per_frame * 2)
            + pcm_frame(1200, samples_per_frame * 4)
            + pcm_frame(0, samples_per_frame * 4)
        )

        events = detector.accept_chunk(AudioChunk(device_id="speaker-1", seq=1, timestamp_ms=0, pcm=pcm))

        self.assertEqual([event.kind for event in events], ["speech_started", "speech_ended"])
        self.assertIsNotNone(events[-1].window)
        self.assertEqual(events[-1].window.device_id, "speaker-1")
        self.assertGreaterEqual(events[-1].window.duration_ms, 180)


class FakeSherpaVad:
    def __init__(self, speech_detected):
        self.speech_detected = list(speech_detected)
        self.index = -1
        self.current_segment = SimpleNamespace(start=0)

    def accept_waveform(self, _frame):
        self.index += 1

    def is_speech_detected(self):
        return self.speech_detected[min(self.index, len(self.speech_detected) - 1)]

    def empty(self):
        return True

    def pop(self):
        return None


class SherpaOnnxEndpointDetectorTest(unittest.TestCase):
    def test_silero_false_does_not_end_until_min_silence_accumulates(self):
        config = EndpointingConfig(sample_rate=16000)
        samples_per_frame = 480
        detector = SherpaOnnxEndpointDetector(
            Path("missing-vad.onnx"),
            config,
            min_silence_seconds=0.09,
            pre_roll_seconds=0,
        )
        detector._vad = FakeSherpaVad([True, False, False, False, False])
        detector._window_size = samples_per_frame
        detector._pending_samples = np.empty(0, dtype=np.float32)
        detector._pending_raw_samples = np.empty(0, dtype=np.int16)

        events = detector.accept_chunk(
            AudioChunk(device_id="speaker-1", seq=1, timestamp_ms=0, pcm=pcm_frame(1200, samples_per_frame))
        )
        self.assertEqual([event.kind for event in events], ["speech_started"])

        events = detector.accept_chunk(
            AudioChunk(device_id="speaker-1", seq=2, timestamp_ms=30, pcm=pcm_frame(900, samples_per_frame))
        )
        self.assertEqual(events, [])

        events = detector.accept_chunk(
            AudioChunk(device_id="speaker-1", seq=3, timestamp_ms=60, pcm=pcm_frame(0, samples_per_frame * 3))
        )

        self.assertEqual([event.kind for event in events], ["speech_ended"])
        self.assertIsNotNone(events[-1].window)
        self.assertGreaterEqual(events[-1].window.duration_ms, 150)


if __name__ == "__main__":
    unittest.main()
