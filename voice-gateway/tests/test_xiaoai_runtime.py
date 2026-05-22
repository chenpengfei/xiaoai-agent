import asyncio
import unittest

from voice_gateway.adapters import InMemoryDeviceController
from voice_gateway.app import MinimalLoopGateway
from voice_gateway.asr import StaticFinalASREngine
from voice_gateway.audio import EndpointEvent
from voice_gateway.hermes import StaticHermesConnector
from voice_gateway.models import AudioWindow
from voice_gateway.observability import InMemoryEventLogger
from voice_gateway.playback import PlaybackManager, StaticTTSEngine
from voice_gateway.xiaoai_runtime import RuntimeState, XiaoAIMinimalRuntime


class RaisingEndpoint:
    def reset(self):
        pass

    def accept_chunk(self, _chunk):
        raise RuntimeError("vad exploded")


class OneShotEndpoint:
    def reset(self):
        pass

    def accept_chunk(self, chunk):
        window = AudioWindow(
            device_id=chunk.device_id,
            start_ms=chunk.timestamp_ms,
            end_ms=chunk.timestamp_ms + 300,
            sample_rate=chunk.sample_rate,
            pcm=chunk.pcm,
        )
        return [
            EndpointEvent(kind="speech_started", timestamp_ms=chunk.timestamp_ms),
            EndpointEvent(kind="speech_ended", window=window, timestamp_ms=window.end_ms),
        ]


class FakeTextAckDevice:
    def __init__(self, ok=True):
        self.ok = ok
        self.texts = []

    async def play_text(self, text, *, timeout_ms=60000):
        self.texts.append({"text": text, "timeout_ms": timeout_ms})
        return self.ok


class XiaoAIMinimalRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_worker_recovers_after_gateway_exception(self):
        events = InMemoryEventLogger()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("你好你是谁"),
            hermes=StaticHermesConnector("我是小马。"),
            playback=PlaybackManager(
                tts=StaticTTSEngine(),
                device=InMemoryDeviceController(),
                events=events,
            ),
            endpoint=OneShotEndpoint(),
            events=events,
        )
        runtime = XiaoAIMinimalRuntime(
            gateway,
            device_id="speaker-1",
            wake_asr=StaticFinalASREngine("你好"),
            wake_endpoint=RaisingEndpoint(),
        )

        await runtime.start()
        runtime._put_nowait(b"\x00\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime._worker_task is not None
        assert not runtime._worker_task.done()
        assert "runtime.worker.failed" in events.names()
        assert "error.recovered" in events.names()

        await runtime.stop()

    async def test_wake_word_sends_text_ack_then_next_utterance_to_hermes(self):
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("我是小马。")
        device = FakeTextAckDevice()
        playback_device = InMemoryDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("你是谁"),
            hermes=hermes,
            playback=PlaybackManager(
                tts=StaticTTSEngine(),
                device=playback_device,
                events=events,
            ),
            endpoint=OneShotEndpoint(),
            events=events,
        )
        runtime = XiaoAIMinimalRuntime(
            gateway,
            device_id="speaker-1",
            wake_asr=StaticFinalASREngine("你好"),
            wake_endpoint=OneShotEndpoint(),
            device=device,
            wake_word="你好",
            wake_ack_texts=("在",),
            ack_suppression_seconds=0,
        )

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.WAIT_QUESTION
        assert device.texts == [{"text": "在", "timeout_ms": 60000}]
        assert hermes.turns == []

        runtime._put_nowait(b"\x02\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.WAIT_WAKE_WORD
        assert len(hermes.turns) == 1
        assert hermes.turns[0].user_text == "你是谁"
        assert len(playback_device.played) == 1

        await runtime.stop()

    async def test_failed_wake_ack_does_not_suppress_question_audio(self):
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("我是小马。")
        device = FakeTextAckDevice(ok=False)
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("我家里有几个人"),
            hermes=hermes,
            playback=PlaybackManager(
                tts=StaticTTSEngine(),
                device=InMemoryDeviceController(),
                events=events,
            ),
            endpoint=OneShotEndpoint(),
            events=events,
        )
        runtime = XiaoAIMinimalRuntime(
            gateway,
            device_id="speaker-1",
            wake_asr=StaticFinalASREngine("你好"),
            wake_endpoint=OneShotEndpoint(),
            device=device,
            wake_word="你好",
            wake_ack_texts=("在",),
            ack_suppression_seconds=10,
        )

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.WAIT_QUESTION
        assert runtime.ignore_audio_until == 0.0

        runtime._put_nowait(b"\x02\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.WAIT_WAKE_WORD
        assert len(hermes.turns) == 1
        assert hermes.turns[0].user_text == "我家里有几个人"

        await runtime.stop()


if __name__ == "__main__":
    unittest.main()
