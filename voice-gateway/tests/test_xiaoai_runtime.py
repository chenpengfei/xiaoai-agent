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
from voice_gateway.xiaoai_runtime import RuntimeState, XiaoAIMinimalRuntime, _play_connected_prompt


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


class ScriptedEndpoint:
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.index = 0

    def reset(self):
        pass

    def accept_chunk(self, chunk):
        script = self.scripts[min(self.index, len(self.scripts) - 1)]
        self.index += 1
        if script == "silence":
            return []
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


class RejectingDeviceController:
    def __init__(self):
        self.played = []

    async def play_audio_resource(self, resource):
        self.played.append(resource)
        return False


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


class QueueDuringAckDeviceController:
    def __init__(self):
        self.played = []
        self.runtime = None

    async def play_audio_resource(self, resource):
        self.played.append(resource)
        if len(self.played) == 1 and self.runtime is not None:
            self.runtime._put_nowait(b"\x02\x00" * 480)
        return True


class QueueDuringAnswerDeviceController:
    def __init__(self):
        self.played = []
        self.runtime = None

    async def play_audio_resource(self, resource):
        self.played.append(resource)
        if len(self.played) == 2 and self.runtime is not None:
            self.runtime._put_nowait(b"\x04\x00" * 480)
        return True


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
            merge_window_seconds=0,
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
            device=playback_device,
            wake_word="你好",
            wake_ack_texts=("在",),
            ack_suppression_seconds=0,
            merge_window_seconds=0,
        )

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.WAIT_QUESTION
        assert len(playback_device.played) == 1
        assert hermes.turns == []

        runtime._put_nowait(b"\x02\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.WAIT_WAKE_WORD
        assert len(hermes.turns) == 1
        assert hermes.turns[0].user_text == "你是谁"
        assert len(playback_device.played) == 2

        await runtime.stop()

    async def test_successful_wake_ack_does_not_suppress_question_audio_by_default(self):
        events = InMemoryEventLogger()
        playback_device = InMemoryDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("家里有几个人"),
            hermes=StaticHermesConnector("三个人。"),
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
            device=playback_device,
            wake_word="你好",
            wake_ack_texts=("在",),
            merge_window_seconds=0,
        )

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.WAIT_QUESTION
        assert runtime.ignore_audio_until == 0.0

        await runtime.stop()

    async def test_question_audio_queued_during_wake_ack_is_preserved(self):
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("三个人。")
        playback_device = QueueDuringAckDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("家里有几个人"),
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
            device=playback_device,
            wake_word="你好",
            wake_ack_texts=("在",),
            merge_window_seconds=0,
        )
        playback_device.runtime = runtime

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.1)

        assert runtime.state == RuntimeState.WAIT_WAKE_WORD
        assert len(hermes.turns) == 1
        assert hermes.turns[0].user_text == "家里有几个人"
        assert len(playback_device.played) == 2

        await runtime.stop()

    async def test_question_audio_queued_after_wake_ack_is_preserved(self):
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("三个人。")
        playback_device = InMemoryDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("家里有几个人"),
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
            device=playback_device,
            wake_word="你好",
            wake_ack_texts=("在",),
            merge_window_seconds=0,
        )

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.05)
        runtime._put_nowait(b"\x02\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.WAIT_WAKE_WORD
        assert len(hermes.turns) == 1
        assert hermes.turns[0].user_text == "家里有几个人"
        assert len(playback_device.played) == 2

        await runtime.stop()

    async def test_split_question_segments_are_merged_before_hermes(self):
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("是挺累的。")
        playback_device = InMemoryDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=SequenceFinalASREngine(["有用的人", "没有一个不累的"]),
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
            device=playback_device,
            wake_word="你好",
            wake_ack_texts=("在",),
            merge_window_seconds=0.05,
        )

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.05)
        runtime._put_nowait(b"\x02\x00" * 480)
        await asyncio.sleep(0.01)
        runtime._put_nowait(b"\x03\x00" * 480)
        await asyncio.sleep(0.1)

        assert runtime.state == RuntimeState.WAIT_WAKE_WORD
        assert len(hermes.turns) == 1
        assert hermes.turns[0].user_text == "有用的人没有一个不累的"
        assert "runtime.question_merged" in events.names()

        await runtime.stop()

    async def test_wake_ack_prefix_is_stripped_from_question_text(self):
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("三个人。")
        playback_device = InMemoryDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("在家里有几个人"),
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
            device=playback_device,
            wake_word="你好",
            wake_ack_texts=("在",),
            merge_window_seconds=0,
        )

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.05)
        runtime._put_nowait(b"\x02\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.WAIT_WAKE_WORD
        assert len(hermes.turns) == 1
        assert hermes.turns[0].user_text == "家里有几个人"
        ack_filter = next(event for event in events.events if event.event == "asr.ack_filter_applied")
        assert ack_filter.fields["action"] == "strip_prefix"
        assert ack_filter.fields["filtered_text"] == "家里有几个人"

        await runtime.stop()

    async def test_short_wake_ack_echo_is_ignored_and_runtime_keeps_waiting_for_question(self):
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("三个人。")
        playback_device = InMemoryDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=SequenceFinalASREngine(["在", "家里有几个人"]),
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
            device=playback_device,
            wake_word="你好",
            wake_ack_texts=("在",),
            merge_window_seconds=0,
        )

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.05)
        runtime._put_nowait(b"\x02\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.WAIT_QUESTION
        assert hermes.turns == []
        assert len(playback_device.played) == 1
        ack_filter = next(event for event in events.events if event.event == "asr.ack_filter_applied")
        assert ack_filter.fields["action"] == "ignore_short"
        assert "runtime.question_ignored" in events.names()

        runtime._put_nowait(b"\x03\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.WAIT_WAKE_WORD
        assert len(hermes.turns) == 1
        assert hermes.turns[0].user_text == "家里有几个人"
        assert len(playback_device.played) == 2

        await runtime.stop()

    async def test_followup_question_does_not_require_wake_word(self):
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("我是小马。")
        playback_device = InMemoryDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=SequenceFinalASREngine(["你是谁", "我刚才问了什么"]),
            hermes=hermes,
            playback=PlaybackManager(
                tts=StaticTTSEngine(),
                device=playback_device,
                events=events,
            ),
            endpoint=OneShotEndpoint(),
            events=events,
            followup_enabled=True,
        )
        runtime = XiaoAIMinimalRuntime(
            gateway,
            device_id="speaker-1",
            wake_asr=StaticFinalASREngine("你好"),
            wake_endpoint=OneShotEndpoint(),
            device=playback_device,
            wake_word="你好",
            wake_ack_texts=("在",),
            followup_timeout_seconds=15,
            merge_window_seconds=0,
            post_playback_ignore_seconds=0,
        )

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.05)
        runtime._put_nowait(b"\x02\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.FOLLOWUP_WAIT
        assert gateway.state.value == "FOLLOWUP_WAIT"
        assert len(hermes.turns) == 1
        conversation_id = hermes.turns[0].conversation_id

        runtime._put_nowait(b"\x03\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.FOLLOWUP_WAIT
        assert len(hermes.turns) == 2
        assert hermes.turns[1].conversation_id == conversation_id
        assert hermes.turns[1].user_text == "我刚才问了什么"
        assert [(item.role, item.content) for item in hermes.turns[1].history] == [
            ("user", "你是谁"),
            ("assistant", "我是小马。"),
        ]
        assert "followup.started" in events.names()

        await runtime.stop()

    async def test_followup_silence_does_not_create_turn(self):
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("我是小马。")
        playback_device = InMemoryDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=SequenceFinalASREngine(["你是谁", "我刚才问了什么"]),
            hermes=hermes,
            playback=PlaybackManager(
                tts=StaticTTSEngine(),
                device=playback_device,
                events=events,
            ),
            endpoint=ScriptedEndpoint(["speech", "silence", "speech"]),
            events=events,
            followup_enabled=True,
        )
        runtime = XiaoAIMinimalRuntime(
            gateway,
            device_id="speaker-1",
            wake_asr=StaticFinalASREngine("你好"),
            wake_endpoint=OneShotEndpoint(),
            device=playback_device,
            wake_word="你好",
            wake_ack_texts=("在",),
            followup_timeout_seconds=15,
            merge_window_seconds=0,
            post_playback_ignore_seconds=0,
        )

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.05)
        runtime._put_nowait(b"\x02\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.FOLLOWUP_WAIT
        assert gateway.state.value == "FOLLOWUP_WAIT"
        assert len(hermes.turns) == 1
        followup_started_count = events.names().count("followup.started")

        runtime._put_nowait(b"\x03\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.FOLLOWUP_WAIT
        assert gateway.state.value == "FOLLOWUP_WAIT"
        assert len(hermes.turns) == 1
        assert events.names().count("followup.started") == followup_started_count
        assert "followup.audio_chunk_received" not in events.names()

        runtime._put_nowait(b"\x04\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.FOLLOWUP_WAIT
        assert len(hermes.turns) == 2
        assert hermes.turns[1].user_text == "我刚才问了什么"

        await runtime.stop()

    async def test_answer_playback_echo_is_drained_and_guarded_before_followup(self):
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("一加二等于三。")
        playback_device = QueueDuringAnswerDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=SequenceFinalASREngine(["一加二等于几", "有用的人没有不累的"]),
            hermes=hermes,
            playback=PlaybackManager(
                tts=StaticTTSEngine(),
                device=playback_device,
                events=events,
            ),
            endpoint=OneShotEndpoint(),
            events=events,
            followup_enabled=True,
        )
        runtime = XiaoAIMinimalRuntime(
            gateway,
            device_id="speaker-1",
            wake_asr=StaticFinalASREngine("你好"),
            wake_endpoint=OneShotEndpoint(),
            device=playback_device,
            wake_word="你好",
            wake_ack_texts=("在",),
            followup_timeout_seconds=15,
            merge_window_seconds=0,
            post_playback_ignore_seconds=0.1,
        )
        playback_device.runtime = runtime

        await runtime.start()
        runtime._put_nowait(b"\x01\x00" * 480)
        await asyncio.sleep(0.05)
        runtime._put_nowait(b"\x02\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.FOLLOWUP_WAIT
        assert len(hermes.turns) == 1
        assert hermes.turns[0].user_text == "一加二等于几"
        assert "runtime.playback_backlog_drained" in events.names()

        runtime._put_nowait(b"\x05\x00" * 480)
        runtime._put_nowait(b"\x06\x00" * 480)
        runtime._put_nowait(b"\x07\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.FOLLOWUP_WAIT
        assert len(hermes.turns) == 1
        assert events.names().count("input_gate.ignored") == 1

        await asyncio.sleep(0.1)
        runtime._put_nowait(b"\x03\x00" * 480)
        await asyncio.sleep(0.05)

        assert runtime.state == RuntimeState.FOLLOWUP_WAIT
        assert len(hermes.turns) == 2
        assert hermes.turns[1].user_text == "有用的人没有不累的"

        await runtime.stop()

    async def test_failed_wake_ack_does_not_suppress_question_audio(self):
        events = InMemoryEventLogger()
        hermes = StaticHermesConnector("我是小马。")
        device = RejectingDeviceController()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine("我家里有几个人"),
            hermes=hermes,
            playback=PlaybackManager(
                tts=StaticTTSEngine(),
                device=device,
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
            merge_window_seconds=0,
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

    async def test_connected_prompt_plays_connected_text(self):
        events = InMemoryEventLogger()
        device = InMemoryDeviceController()
        tts = StaticTTSEngine()
        gateway = MinimalLoopGateway(
            device_id="speaker-1",
            asr=StaticFinalASREngine(""),
            hermes=StaticHermesConnector(""),
            playback=PlaybackManager(
                tts=tts,
                device=device,
                events=events,
            ),
            endpoint=OneShotEndpoint(),
            events=events,
        )

        await _play_connected_prompt(gateway, "speaker-1")

        assert tts.texts == ["已连接"]
        assert len(device.played) == 1
        connected = next(event for event in events.events if event.event == "device.connected")
        assert connected.fields["text"] == "已连接"
        assert connected.fields["ok"] is True


if __name__ == "__main__":
    unittest.main()
