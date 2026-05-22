import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_gateway.config import TTSConfig
from voice_gateway.playback import EdgeTTSFileEngine


class EdgeTTSFileEngineTest(unittest.TestCase):
    def test_edge_tts_error_includes_stderr(self):
        engine = EdgeTTSFileEngine(TTSConfig(output_dir=Path("/tmp"), http_base_url="http://127.0.0.1:8765"))

        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(
                returncode=2,
                cmd=["edge_tts"],
                stderr="network unavailable",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "network unavailable"):
                engine._run_edge_tts("你好", Path("/tmp/out.mp3"))


if __name__ == "__main__":
    unittest.main()
