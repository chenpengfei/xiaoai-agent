import unittest

from voice_gateway.audio import EnergyEndpointDetector
from voice_gateway.config import EndpointingConfig
from voice_gateway.models import AudioChunk


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


if __name__ == "__main__":
    unittest.main()
