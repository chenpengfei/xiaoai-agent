# 02 最小闭环

本文定义 `voice-gateway` 的第一阶段能力：从音箱麦克风音频到 Hermes 回答，再到音箱播报。

上级索引：[Voice Gateway 总设计](./DESIGN.md)  
前置文档：[01 架构与模块边界](./01-architecture.md)  
后续文档：[03 连续对话](./03-continuous-conversation.md)

## 1. 阶段目标

最小闭环只解决一件事：

```text
音箱 PCM 音频
  -> Mac Mini VAD 切出一句话
  -> Mac Mini ASR 转文字
  -> Hermes 生成回答
  -> 音箱播报回答
```

这一阶段不做连续对话、不做自然打断、不做声纹识别、不做复杂 TTS 音频流。

## 2. 设计原则

- 只启用最少状态。
- 只处理单轮问答。
- 只接一个音箱连接。
- ASR 先以 final result 为主，不要求真正流式识别。
- 播放由 PlaybackManager 管理，不要求第一阶段实现低延迟流式播放。
- 输入只来自 AudioIngest 标准化后的用户语音片段。

## 3. 启用模块

```text
XiaoAIProtocolAdapter
AudioIngest
VAD / Endpointing
ASR Pipeline
HermesConnector
PlaybackManager
```

暂不启用：

```text
SpeakerIdentity
FOLLOWUP_WAIT
INTERRUPTED
low-latency audio streaming
AEC
```

## 4. 状态机

```text
IDLE
  -> LISTENING       on WakeupDetected

LISTENING
  -> ENDPOINTING     on SpeechStarted
  -> IDLE            on ListenTimeout

ENDPOINTING
  -> THINKING        on SpeechEnded + ASRReady
  -> IDLE            on ASRFailed

THINKING
  -> SPEAKING        on HermesResponseReady
  -> IDLE            on HermesFailed

SPEAKING
  -> IDLE            on PlaybackFinished
```

最小闭环的回答结束后直接回到 `IDLE`。

## 5. 音频输入

音箱通过 voice-gateway speaker client 持续发送：

```text
Stream(tag="record", bytes=pcm)
```

gateway 将其转换为：

```python
AudioChunk:
    device_id: str
    seq: int
    timestamp_ms: int
    sample_rate: 16000
    channels: 1
    sample_format: "s16le"
    pcm: bytes
```

## 6. VAD 切句

最小闭环只需要基础端点检测：

```text
人声持续超过 min_speech_ms
  -> SpeechStarted

尾部静音持续超过 min_silence_ms
  -> SpeechEnded
```

输出：

```python
AudioWindow:
    start_ms: int
    end_ms: int
    pcm: bytes
```

## 7. ASR

第一阶段推荐只使用 final ASR：

```text
AudioWindow -> ASRResult
```

接口：

```python
ASRResult:
    text: str
    normalized_text: str
    is_final: True
    engine: str
```

模型选择：

- 首选 sherpa-onnx。
- 如果实现成本更低，也可以先用 mlx-whisper final transcription。

## 8. Hermes 调用

HermesConnector 接收：

```python
HermesTurn:
    conversation_id: str
    user_text: str
    speaker: None
    history: []
```

输出：

```python
HermesResponse:
    text: str
    should_speak: True
```

最小闭环不做多轮记忆注入，只保留必要系统提示。

## 9. 播放策略

第一阶段播放使用 gateway 管理的回答音频资源：

```text
HermesResponse.text
  -> TTSEngine
  -> PlaybackResource
  -> PlaybackManager
  -> PlayAudioResourceCommand
```

原因：

- 先建立稳定的端到端问答闭环。
- 让状态机只关心播放开始、结束和失败事件。
- 后续由 [06 TTS 与播放控制](./06-tts-playback.md) 扩展取消、打断、参考音频和低延迟播放。

## 10. 阶段完成后的能力

完成本阶段后，系统具备：

- 不依赖外部云 ASR 的单轮语音问答。
- Mac Mini 侧 VAD 切句。
- Mac Mini 侧 ASR。
- Hermes 回答。
- 音箱播报。

下一阶段在此基础上加入 follow-up window，详见 [03 连续对话](./03-continuous-conversation.md)。
