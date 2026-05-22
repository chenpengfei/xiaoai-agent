# 07 可观测性

本文定义 `voice-gateway` 的日志事件、指标、trace 和故障诊断原则。

上级索引：[Voice Gateway 总设计](./DESIGN.md)  
相关文档：[01 架构与模块边界](./01-architecture.md)

## 1. 阶段目标

可观测性解决的问题是：

```text
系统正在发生什么？
一次语音请求卡在哪里？
失败发生在哪个模块？
性能瓶颈来自哪里？
```

`voice-gateway` 的主链路跨越音箱、音频流、VAD、ASR、Hermes、TTS 和播放控制。没有统一观测时，问题会很难定位。

本阶段目标是让每一轮语音交互都可以被追踪、度量和复盘。

## 2. 观测对象

需要观测的核心对象：

- device：音箱连接和设备状态。
- audio stream：录音流和播放流。
- conversation：一次连续对话窗口。
- turn：用户的一轮输入和助手的一轮回答。
- playback：一次播放会话。
- model call：ASR、声纹、Hermes、TTS 调用。

建议统一使用这些 ID：

```text
device_id
session_id
conversation_id
turn_id
playback_id
stream_id
```

其中：

- `session_id` 用于一次从唤醒到结束的请求追踪。
- `conversation_id` 用于多轮连续对话。
- `turn_id` 用于一轮用户输入。
- `playback_id` 用于一次回答播放。

## 3. 结构化事件

系统应输出结构化事件，而不是只依赖自然语言日志。

事件类别：

- 设备连接。
- 音频流健康度。
- VAD 状态。
- ASR 结果。
- 声纹识别结果。
- 对话状态转移。
- Hermes 请求与响应元信息。
- TTS 生成生命周期。
- 播放生命周期。
- 打断判断。
- 错误恢复。

示例：

```json
{
  "event": "dialogue.transition",
  "device_id": "speaker_living_room",
  "conversation_id": "c_...",
  "turn_id": "t_...",
  "from": "SPEAKING",
  "to": "INTERRUPTED",
  "reason": "barge_in_confirmed",
  "timestamp_ms": 123456789
}
```

## 4. 关键事件

建议优先实现这些事件：

```text
device.connected
device.disconnected
audio.chunk.received
audio.stream.gap
vad.speech_started
vad.speech_ended
asr.started
asr.completed
asr.failed
speaker_identity.completed
dialogue.transition
hermes.started
hermes.completed
hermes.failed
tts.started
tts.completed
tts.failed
playback.started
playback.finished
playback.stopped
playback.failed
barge_in.candidate
barge_in.confirmed
error.recovered
```

事件命名应稳定，避免把临时调试文本变成下游依赖。

## 5. 指标

建议记录：

```text
audio.chunk.bytes
audio.chunk.gap_ms
vad.speech_probability
vad.endpoint_latency_ms
asr.latency_ms
speaker_identity.latency_ms
hermes.latency_ms
tts.latency_ms
playback.start_latency_ms
playback.duration_ms
dialogue.turn.total_ms
dialogue.turn.count
barge_in.count
barge_in.latency_ms
error.count
```

这些指标用于回答三类问题：

- 主链路是否健康。
- 端到端响应是否够快。
- 哪个模块是当前瓶颈。

## 6. Trace

每一轮请求应能串起完整 trace：

```text
WakeupDetected
  -> SpeechStarted
  -> SpeechEnded
  -> ASRReady
  -> HermesResponseReady
  -> TTSReady
  -> PlaybackStarted
  -> PlaybackFinished
```

连续对话阶段，多个 `turn_id` 应归属于同一个 `conversation_id`。

自然打断阶段，旧 `playback_id` 被停止后，新一轮 `turn_id` 应该能清楚关联到 `barge_in.confirmed`。

## 7. 故障诊断

预期故障：

- 音箱 WebSocket 断开。
- 音频块停止到达。
- 音频块间隔异常。
- VAD 误判。
- ASR 超时或返回空文本。
- Hermes 超时。
- TTS 生成失败。
- 播放命令失败。
- 打断后旧播放事件迟到。

处理原则：

- 设备故障不能导致 gateway 进程崩溃。
- 模型故障只影响当前 turn。
- Hermes 失败时，如果播放可用，应给出简短失败反馈。
- 连续空 ASR 应回到 `IDLE`。
- 打断时必须取消旧 TTS、旧播放和过期 Hermes 响应。
- 断线重连后清理旧 conversation 的播放和监听状态。

## 8. 验收标准

本阶段完成标准：

- 每一轮语音请求都有 `session_id` 或 `conversation_id`。
- 关键状态转移都有结构化事件。
- ASR、Hermes、TTS、播放都有耗时指标。
- 音频流中断或卡顿可以从日志中定位。
- 打断可以看到 candidate、confirmed、playback stopped 的完整链路。
- 常见失败可以定位到具体模块。
- 失败恢复后能看到明确的 `error.recovered` 或等价事件。
