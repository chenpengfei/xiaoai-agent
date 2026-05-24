import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice_gateway.config import TTSConfig
from voice_gateway.playback import EdgeTTSFileEngine, build_tts_engine
from voice_gateway.playback.base import DEFAULT_CACHED_TTS_TEXTS


def asyncio_run(coro):
    return asyncio.run(coro)


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

    def test_common_texts_use_stable_cache_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EdgeTTSFileEngine(TTSConfig(output_dir=Path(tmp), http_base_url="http://127.0.0.1:8765"))

            def write_file(_text, path):
                Path(path).write_bytes(b"mp3")

            with patch.object(engine, "_run_edge_tts", side_effect=write_file) as run_edge_tts:
                for text in DEFAULT_CACHED_TTS_TEXTS:
                    with self.subTest(text=text):
                        first = asyncio_run(engine.synthesize_file(text))
                        second = asyncio_run(engine.synthesize_file(text))
                        self.assertEqual(first.local_path, second.local_path)
                        self.assertEqual(first.url, second.url)
                        self.assertIn("/cache/", first.url)

            self.assertEqual(run_edge_tts.call_count, len(DEFAULT_CACHED_TTS_TEXTS))

    def test_warm_cache_preloads_common_texts(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EdgeTTSFileEngine(TTSConfig(output_dir=Path(tmp), http_base_url="http://127.0.0.1:8765"))

            def write_file(_text, path):
                Path(path).write_bytes(b"mp3")

            with patch.object(engine, "_run_edge_tts", side_effect=write_file) as run_edge_tts:
                asyncio_run(engine.warm_cache())
                for text in DEFAULT_CACHED_TTS_TEXTS:
                    asyncio_run(engine.synthesize_file(text))

            self.assertEqual(run_edge_tts.call_count, len(DEFAULT_CACHED_TTS_TEXTS))

    def test_non_cached_text_uses_new_file_each_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = EdgeTTSFileEngine(TTSConfig(output_dir=Path(tmp), http_base_url="http://127.0.0.1:8765"))

            def write_file(_text, path):
                Path(path).write_bytes(b"mp3")

            with patch.object(engine, "_run_edge_tts", side_effect=write_file) as run_edge_tts:
                first = asyncio_run(engine.synthesize_file("非缓存文本"))
                second = asyncio_run(engine.synthesize_file("非缓存文本"))

            self.assertEqual(run_edge_tts.call_count, 2)
            self.assertNotEqual(first.local_path, second.local_path)


class BuildTTSEngineTest(unittest.TestCase):
    def test_build_edge_engine_by_default(self):
        engine = build_tts_engine(TTSConfig())

        self.assertIsInstance(engine, EdgeTTSFileEngine)


if __name__ == "__main__":
    unittest.main()
