import unittest

from server.adapters import XiaoAIProtocolAdapter, XiaoAIStream
from server.models import PlaybackResource


class XiaoAIProtocolAdapterTest(unittest.TestCase):
    def test_record_stream_becomes_audio_chunk(self):
        adapter = XiaoAIProtocolAdapter()
        chunk = adapter.audio_chunk_from_stream(
            XiaoAIStream(tag="record", payload=b"\x00\x00", device_id="speaker-1", seq=7, timestamp_ms=123)
        )

        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.device_id, "speaker-1")
        self.assertEqual(chunk.seq, 7)
        self.assertEqual(chunk.sample_rate, 16000)
        self.assertEqual(chunk.pcm, b"\x00\x00")

    def test_playback_resource_command_uses_start_play_url(self):
        command = XiaoAIProtocolAdapter().play_audio_resource_command(
            PlaybackResource(playback_id="p_1", url="http://127.0.0.1/a.mp3", format="mp3")
        )

        self.assertEqual(command["method"], "start_play")
        self.assertEqual(command["params"]["url"], "http://127.0.0.1/a.mp3")


if __name__ == "__main__":
    unittest.main()
