# 01 架构与模块边界

本文定义 `voice-gateway` 的长期系统架构。后续阶段文档都以这里的模块边界和数据结构为准。

上级索引：[Voice Gateway 总设计](./DESIGN.md)

## 1. 系统边界

`voice-gateway` 运行在 Mac Mini 上，承接音箱传来的麦克风音频流，并负责语音理解、对话状态、Hermes 调用和播放控制。

当前音箱侧运行已验证过的 Rust client 协议；长期目标是把刷机后的音箱端 client、安装脚本和设备端 KWS 收口到 `voice-gateway` 工程。刷机和固件 patch 继续指引到原 [open-xiaoai](https://github.com/idootop/open-xiaoai) 仓库，详见 [08 音箱端 Client 与 KWS](./08-device-lifecycle.md)。

音箱侧 client 负责：

- 建立 WebSocket 连接。
- 执行 `start_recording`、`start_play`、`stop_play`、`run_shell` 等 RPC。
- 在设备侧完成基础唤醒、语音活动检测和音频采集控制。
- 通过 `arecord` 采集 PCM。
- 通过 `aplay` 播放音频。
- 上报设备连接、录音和播放状态事件。

Mac Mini 侧负责：

- 音频接入和缓冲。
- VAD 与端点检测。
- ASR。
- 声纹识别。
- 对话状态机。
- Hermes 调用。
- TTS 和播放策略。
- 打断判断。
- 安全、隐私和可观测性。

## 2. 模块图

```text
小爱音箱 / XiaoAI device client
  -> XiaoAIProtocolAdapter
  -> AudioIngest
  -> VAD / Endpointing
  -> ASR Pipeline
  -> DialogueStateMachine
  -> HermesConnector
  -> TTSEngine
  -> PlaybackManager
  -> DeviceController
  -> XiaoAIProtocolAdapter
  -> 小爱音箱播放

DeviceStateChanged
  -> XiaoAIProtocolAdapter
  -> DialogueStateMachine

AudioWindow
  -> SpeakerIdentity
  -> DialogueStateMachine

PlaybackManager
  -> PlaybackStarted / PlaybackFinished / PlaybackStopped / PlaybackFailed
  -> DialogueStateMachine
```

## 3. XiaoAIProtocolAdapter

负责 Mac Mini 与音箱之间的传输协议。

输入映射：

```text
Stream(tag="record", bytes=pcm)
  -> AudioChunkReceived

Event(event="device_state", data=...)
  -> DeviceStateChanged

Event(event="playing", data=...)
  -> SpeakerPlaybackStateChanged
```

事件归一化规则：

- `DeviceStateChanged` 是设备侧状态事件的统一入口，不把设备私有字段传入业务状态机。
- 当设备状态表示唤醒、KWS 命中、录音会话开始或等价的用户输入开始信号时，Adapter 归一化为 `WakeupDetected`。
- `WakeupDetected` 只表示进入 `LISTENING` 的触发，不代表已经拿到可用文本。
- `Stream(tag="record")` 始终归一化为 `AudioChunkReceived`，后续由 AudioIngest、VAD 和 Endpointing 生成 `SpeechStarted` / `SpeechEnded`。
- `Event(event="playing")` 归一化为 `SpeakerPlaybackStateChanged`，只描述设备播放状态，不直接驱动对话内容。

输出映射：

```text
StartCaptureCommand
  -> RPC start_recording

StartPlaybackCommand
  -> RPC start_play

StopPlaybackCommand
  -> RPC stop_play

PlayAudioResourceCommand
  -> RPC start_play(url=...)

AudioPlaybackChunkCommand
  -> Stream(tag="play", bytes=pcm)

RunSpeakerShellCommand
  -> RPC run_shell
```

这个模块只处理协议，不包含 VAD、ASR、Hermes 或对话逻辑。

## 4. AudioIngest

负责把来自音箱的音频块整理成稳定的内部音频流。

内部标准格式：

```text
sample_rate: 16000
channels: 1
sample_format: s16le
encoding: pcm
```

核心结构：

```python
AudioChunk:
    device_id: str
    stream_id: str
    seq: int
    timestamp_ms: int
    sample_rate: int
    channels: int
    sample_format: str
    pcm: bytes

AudioWindow:
    device_id: str
    start_ms: int
    end_ms: int
    sample_rate: int
    pcm: bytes
```

播放资源结构：

```python
PlaybackResource:
    playback_id: str
    url: str
    format: str
    sample_rate: int | None
    channels: int | None
    duration_ms: int | None
```

## 5. VAD 与端点检测

VAD 引擎只输出帧级人声判断或 speech probability。端点检测由 gateway 自己做，用于生成 `SpeechStarted`、`SpeechEnded` 和 `BargeInCandidate`。

候选引擎：

- WebRTC VAD。
- Silero VAD。
- sherpa-onnx VAD。

VAD 设计细节在后续阶段中逐步使用：

- 最小闭环只需要普通端点检测：[02 最小闭环](./02-minimal-loop.md)
- 连续对话需要 follow-up 窗口：[03 连续对话](./03-continuous-conversation.md)
- 自然打断需要播放中 VAD 策略：[04 自然打断](./04-barge-in.md)

## 6. ASR Pipeline

ASR 通过接口隔离：

```python
class ASREngine:
    async def accept_audio(self, chunk: AudioChunk) -> None: ...
    async def transcribe_final(self, window: AudioWindow) -> ASRResult: ...
    async def reset(self) -> None: ...
```

输出：

```python
ASRResult:
    text: str
    normalized_text: str
    language: str | None
    confidence: float | None
    start_ms: int
    end_ms: int
    is_final: bool
    engine: str
```

模型组合建议：

- 实时主链路：sherpa-onnx。
- 可选最终精修：mlx-whisper。
- 可选中文增强：FunASR / SenseVoiceSmall。

## 7. 对话状态机

长期状态：

```text
IDLE
LISTENING
ENDPOINTING
THINKING
SPEAKING
FOLLOWUP_WAIT
INTERRUPTED
ERROR_RECOVERY
```

各阶段只启用其中一部分：

- [02 最小闭环](./02-minimal-loop.md)：`IDLE -> LISTENING -> ENDPOINTING -> THINKING -> SPEAKING -> IDLE`
- [03 连续对话](./03-continuous-conversation.md)：加入 `FOLLOWUP_WAIT`
- [04 自然打断](./04-barge-in.md)：加入 `INTERRUPTED`

## 8. 数据模型

```python
Conversation:
    conversation_id: str
    device_id: str
    room_id: str | None
    active_speaker_id: str | None
    state: DialogueState
    turns: list[Turn]
    created_at_ms: int
    updated_at_ms: int

Turn:
    turn_id: str
    speaker: SpeakerIdentity | None
    audio_window: AudioWindow
    asr: ASRResult
    hermes_response: HermesResponse | None
    state: "captured" | "transcribed" | "answered" | "played" | "interrupted" | "failed"
```

## 9. 目标目录结构

```text
voice-gateway/
  README.md
  DESIGN.md
  docs/
  pyproject.toml
  server/
    adapters/
    audio/
    asr/
    identity/
    dialogue/
    hermes/
    playback/
    observability/
  client/
    client-rust/
    kws/
  tests/
```
