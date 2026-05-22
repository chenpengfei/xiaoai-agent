import unittest

from voice_gateway.adapters import InMemoryDeviceController
from voice_gateway.app import MinimalLoopGateway
from voice_gateway.asr import StaticFinalASREngine
from voice_gateway.audio import EnergyEndpointDetector
from voice_gateway.config import EndpointingConfig
from voice_gateway.hermes import StaticHermesConnector
from voice_gateway.models import AudioChunk, DialogueState
from voice_gateway.observability import InMemoryEventLogger
from voice_gateway.playback import PlaybackManager, StaticTTSEngine


def pcm_frame(amplitude: int, frames: int) -> bytes:
    sample = amplitude.to_bytes(2, "little", signed=True)
    return sample * frames


def utterance_pcm(config: EndpointingConfig) -> bytes:
    samples = int(config.sample_rate * config.frame_ms / 1000)
    return (
        pcm_frame(0, samples * 2)
        + pcm_frame(1400, samples * 5)
        + pcm_frame(0, samples * 5)
    )


class MinimalLoopGatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_single_turn_returns_to_idle_after_playback(self):
        endpoint_config = EndpointingConfig(
            frame_ms=30,
            speech_rms_threshold=450,
            min_speech_ms=90,
            min_silence_ms=90,
        )
        events = InMemoryEventLogger()
        device = InMemoryDeviceController()
        tts = StaticTTSEngine(url="http://127.0.0.1:8765/answer.mp3")
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("你好你是谁"),
            hermes=StaticHermesConnector("我是小马。"),
            playback=PlaybackManager(tts=tts, device=device, events=events),
            endpoint=EnergyEndpointDetector(endpoint_config),
            events=events,
        )

        await gateway.wakeup()
        result = await gateway.accept_audio(
            AudioChunk(
                device_id="speaker-1",
                seq=1,
                timestamp_ms=0,
                pcm=utterance_pcm(endpoint_config),
            )
        )

        assert result is not None
        assert result.state == "played"
        assert result.asr is not None
        assert result.asr.normalized_text == "你好你是谁"
        assert result.hermes_response is not None
        assert result.hermes_response.text == "我是小马。"
        assert gateway.state == DialogueState.IDLE
        assert tts.texts == ["我是小马。"]
        assert len(device.played) == 1

        names = events.names()
        assert "wakeup.detected" in names
        assert "vad.speech_started" in names
        assert "vad.speech_ended" in names
        assert "asr.completed" in names
        assert "hermes.completed" in names
        assert "playback.finished" in names
        hermes_started = next(event for event in events.events if event.event == "hermes.started")
        hermes_completed = next(event for event in events.events if event.event == "hermes.completed")
        assert hermes_started.fields["user_text"] == "你好你是谁"
        assert hermes_completed.fields["response_text"] == "我是小马。"
        assert hermes_completed.fields["should_speak"] is True
        assert hermes_completed.fields["model"] == "static"

    async def test_empty_asr_recovers_to_idle_without_hermes_or_playback(self):
        endpoint_config = EndpointingConfig(
            frame_ms=30,
            speech_rms_threshold=450,
            min_speech_ms=90,
            min_silence_ms=90,
        )
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("不应该调用")
        device = InMemoryDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine(""),
            hermes=hermes,
            playback=PlaybackManager(tts=StaticTTSEngine(), device=device, events=events),
            endpoint=EnergyEndpointDetector(endpoint_config),
            events=events,
        )

        await gateway.wakeup()
        result = await gateway.accept_audio(
            AudioChunk(
                device_id="speaker-1",
                seq=1,
                timestamp_ms=0,
                pcm=utterance_pcm(endpoint_config),
            )
        )

        assert result is not None
        assert result.state == "failed"
        assert result.error == "empty_asr"
        assert gateway.state == DialogueState.IDLE
        assert hermes.turns == []
        assert device.played == []

    async def test_empty_trigger_question_prompts_locally_without_hermes(self):
        endpoint_config = EndpointingConfig(
            frame_ms=30,
            speech_rms_threshold=450,
            min_speech_ms=90,
            min_silence_ms=90,
        )
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("不应该调用")
        device = InMemoryDeviceController()
        tts = StaticTTSEngine()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("你好"),
            hermes=hermes,
            playback=PlaybackManager(tts=tts, device=device, events=events),
            endpoint=EnergyEndpointDetector(endpoint_config),
            events=events,
            asr_text_transform=lambda _asr: "",
        )

        await gateway.wakeup()
        result = await gateway.accept_audio(
            AudioChunk(
                device_id="speaker-1",
                seq=1,
                timestamp_ms=0,
                pcm=utterance_pcm(endpoint_config),
            )
        )

        assert result is not None
        assert result.state == "played"
        assert hermes.turns == []
        assert tts.texts == ["你想问什么？"]
        assert len(device.played) == 1
        assert "asr.empty_question" in events.names()


if __name__ == "__main__":
    unittest.main()
