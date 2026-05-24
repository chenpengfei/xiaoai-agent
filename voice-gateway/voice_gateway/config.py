from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EndpointingConfig:
    sample_rate: int = 16000
    frame_ms: int = 30
    speech_rms_threshold: int = 450
    min_speech_ms: int = 250
    min_silence_ms: int = 500
    max_speech_ms: int = 10000


@dataclass(frozen=True)
class HermesConfig:
    base_url: str = "http://127.0.0.1:8642/v1"
    model: str = "hermes-agent"
    api_key: str = ""
    timeout_seconds: float = 90
    max_tokens: int = 350


@dataclass(frozen=True)
class TTSConfig:
    output_dir: Path = Path("audio-samples/tts")
    http_base_url: str = "http://127.0.0.1:8765"
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"


@dataclass(frozen=True)
class GatewayConfig:
    endpointing: EndpointingConfig = EndpointingConfig()
    hermes: HermesConfig = HermesConfig()
    tts: TTSConfig = TTSConfig()
    sherpa_model_dir: Path = Path("../models/sherpa-onnx-paraformer-zh-2024-03-09")
    silero_vad_model: Path = Path("config/silero_vad.onnx")


def load_config_from_env() -> GatewayConfig:
    return GatewayConfig(
        endpointing=EndpointingConfig(
            speech_rms_threshold=int(os.getenv("VOICE_GATEWAY_SPEECH_RMS_THRESHOLD", "450")),
            min_speech_ms=int(os.getenv("VOICE_GATEWAY_MIN_SPEECH_MS", "250")),
            min_silence_ms=int(os.getenv("VOICE_GATEWAY_MIN_SILENCE_MS", "500")),
            max_speech_ms=int(os.getenv("VOICE_GATEWAY_MAX_SPEECH_MS", "10000")),
        ),
        hermes=HermesConfig(
            base_url=os.getenv("VOICE_GATEWAY_OPENAI_BASE_URL", "http://127.0.0.1:8642/v1").rstrip("/"),
            model=os.getenv("VOICE_GATEWAY_OPENAI_MODEL", "hermes-agent"),
            api_key=os.getenv("VOICE_GATEWAY_OPENAI_API_KEY", ""),
            timeout_seconds=float(os.getenv("VOICE_GATEWAY_OPENAI_TIMEOUT", "90")),
            max_tokens=int(os.getenv("VOICE_GATEWAY_OPENAI_MAX_TOKENS", "350")),
        ),
        tts=TTSConfig(
            output_dir=Path(os.getenv("VOICE_GATEWAY_TTS_OUTPUT_DIR", "audio-samples/tts")),
            http_base_url=os.getenv("VOICE_GATEWAY_TTS_HTTP_BASE_URL", "http://127.0.0.1:8765").rstrip("/"),
            voice=os.getenv("VOICE_GATEWAY_TTS_VOICE", "zh-CN-XiaoxiaoNeural"),
            rate=os.getenv("VOICE_GATEWAY_TTS_RATE", "+0%"),
        ),
        sherpa_model_dir=Path(
            os.getenv("VOICE_GATEWAY_SHERPA_MODEL_DIR", "../models/sherpa-onnx-paraformer-zh-2024-03-09")
        ),
        silero_vad_model=Path(
            os.getenv("VOICE_GATEWAY_SILERO_VAD_MODEL", "config/silero_vad.onnx")
        ),
    )
