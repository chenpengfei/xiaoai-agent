import unittest

from server.adapters import InMemoryDeviceController
from server.app import MinimalLoopGateway
from server.asr import StaticFinalASREngine
from server.audio import EnergyEndpointDetector
from server.config import EndpointingConfig
from server.hermes import StaticHermesConnector
from server.models import AudioChunk, DialogueState
from server.observability import InMemoryEventLogger
from server.playback import PlaybackManager, StaticTTSEngine


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


class SequenceFinalASREngine:
    def __init__(self, texts):
        self.texts = list(texts)
        self.index = 0

    async def accept_audio(self, _chunk):
        return None

    async def transcribe_final(self, window):
        text = self.texts[min(self.index, len(self.texts) - 1)]
        self.index += 1
        return await StaticFinalASREngine(text).transcribe_final(window)

    async def reset(self):
        return None


class FailingHermesConnector:
    async def ask(self, _turn):
        raise RuntimeError("hermes unavailable")


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
        turn_completed = next(event for event in events.events if event.event == "turn.completed")
        assert hermes_started.fields["user_text"] == "你好你是谁"
        assert hermes_completed.fields["response_text"] == "我是小马。"
        assert hermes_completed.fields["should_speak"] is True
        assert hermes_completed.fields["model"] == "static"
        assert turn_completed.fields["trace_id"]
        assert turn_completed.fields["total_ms"] >= 0
        assert turn_completed.fields["stage_ms"]["asr"] >= 0
        assert turn_completed.fields["stage_ms"]["hermes"] >= 0
        assert turn_completed.fields["slowest_stage"] in turn_completed.fields["stage_ms"]

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
        turn_failed = next(event for event in events.events if event.event == "turn.failed")
        assert turn_failed.fields["failed_stage"] == "asr"
        assert turn_failed.fields["failure_reason"] == "empty_asr"
        assert turn_failed.fields["last_successful_stage"] == "asr"
        assert turn_failed.fields["stage_ms"]["asr"] >= 0

    async def test_hermes_failure_emits_hermes_failed_and_turn_failed(self):
        endpoint_config = EndpointingConfig(
            frame_ms=30,
            speech_rms_threshold=450,
            min_speech_ms=90,
            min_silence_ms=90,
        )
        events = InMemoryEventLogger()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("一加二等于几"),
            hermes=FailingHermesConnector(),
            playback=PlaybackManager(
                tts=StaticTTSEngine(),
                device=InMemoryDeviceController(),
                events=events,
            ),
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
        assert result.error == "hermes unavailable"
        assert gateway.state == DialogueState.IDLE

        hermes_failed = next(event for event in events.events if event.event == "hermes.failed")
        turn_failed = next(event for event in events.events if event.event == "turn.failed")
        assert hermes_failed.fields["error_type"] == "RuntimeError"
        assert hermes_failed.fields["error"] == "hermes unavailable"
        assert hermes_failed.fields["user_text"] == "一加二等于几"
        assert hermes_failed.fields["latency_ms"] >= 0
        assert turn_failed.fields["failed_stage"] == "hermes"
        assert turn_failed.fields["failure_reason"] == "RuntimeError"
        assert turn_failed.fields["last_successful_stage"] == "asr"

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

    async def test_followup_turn_reuses_conversation_and_sends_history(self):
        endpoint_config = EndpointingConfig(
            frame_ms=30,
            speech_rms_threshold=450,
            min_speech_ms=90,
            min_silence_ms=90,
        )
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("我是小马。")
        device = InMemoryDeviceController()
        tts = StaticTTSEngine()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=SequenceFinalASREngine(["你是谁", "我刚才问了什么"]),
            hermes=hermes,
            playback=PlaybackManager(tts=tts, device=device, events=events),
            endpoint=EnergyEndpointDetector(endpoint_config),
            events=events,
            followup_enabled=True,
        )

        await gateway.wakeup()
        first = await gateway.accept_audio(
            AudioChunk(
                device_id="speaker-1",
                seq=1,
                timestamp_ms=0,
                pcm=utterance_pcm(endpoint_config),
            )
        )

        assert first is not None
        assert first.state == "played"
        assert gateway.state == DialogueState.FOLLOWUP_WAIT
        assert len(hermes.turns) == 1
        assert hermes.turns[0].history == ()
        conversation_id = first.conversation_id

        await gateway.begin_followup_turn()
        second = await gateway.accept_audio(
            AudioChunk(
                device_id="speaker-1",
                seq=2,
                timestamp_ms=1000,
                pcm=utterance_pcm(endpoint_config),
            )
        )

        assert second is not None
        assert second.state == "played"
        assert gateway.state == DialogueState.FOLLOWUP_WAIT
        assert second.conversation_id == conversation_id
        assert len(hermes.turns) == 2
        assert hermes.turns[1].user_text == "我刚才问了什么"
        assert [(item.role, item.content) for item in hermes.turns[1].history] == [
            ("user", "你是谁"),
            ("assistant", "我是小马。"),
        ]
        assert any(event.event == "followup.started" for event in events.events)

    async def test_followup_timeout_clears_conversation(self):
        endpoint_config = EndpointingConfig(
            frame_ms=30,
            speech_rms_threshold=450,
            min_speech_ms=90,
            min_silence_ms=90,
        )
        events = InMemoryEventLogger()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("你是谁"),
            hermes=StaticHermesConnector("我是小马。"),
            playback=PlaybackManager(tts=StaticTTSEngine(), device=InMemoryDeviceController(), events=events),
            endpoint=EnergyEndpointDetector(endpoint_config),
            events=events,
            followup_enabled=True,
        )
        await gateway.wakeup()
        await gateway.accept_audio(
            AudioChunk(
                device_id="speaker-1",
                seq=1,
                timestamp_ms=0,
                pcm=utterance_pcm(endpoint_config),
            )
        )

        assert gateway.state == DialogueState.FOLLOWUP_WAIT
        assert gateway.conversation_id is not None

        await gateway.followup_timeout()

        assert gateway.state == DialogueState.IDLE
        assert gateway.conversation_id is None
        assert gateway.history == []
        assert "followup.timeout" in events.names()

    async def test_history_keeps_latest_10_turns_by_default(self):
        endpoint_config = EndpointingConfig(
            frame_ms=30,
            speech_rms_threshold=450,
            min_speech_ms=90,
            min_silence_ms=90,
        )
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("你是谁"),
            hermes=StaticHermesConnector("我是小马。"),
            playback=PlaybackManager(
                tts=StaticTTSEngine(),
                device=InMemoryDeviceController(),
                events=InMemoryEventLogger(),
            ),
            endpoint=EnergyEndpointDetector(endpoint_config),
        )

        for index in range(12):
            gateway._append_history(f"问题{index}", f"回答{index}")

        assert gateway._history_turns() == 10
        assert [(item.role, item.content) for item in gateway.history[:2]] == [
            ("user", "问题2"),
            ("assistant", "回答2"),
        ]
        assert [(item.role, item.content) for item in gateway.history[-2:]] == [
            ("user", "问题11"),
            ("assistant", "回答11"),
        ]


if __name__ == "__main__":
    unittest.main()
