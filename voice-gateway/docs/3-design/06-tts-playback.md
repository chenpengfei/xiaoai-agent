# 06 TTS 与播放控制

本文定义回答如何从 Mac Mini 回到音箱播放，以及如何为自然打断提供可控播放能力。

上级索引：[Voice Gateway 总设计](./DESIGN.md)  
前置文档：[02 最小闭环](./02-minimal-loop.md)  
相关文档：[04 自然打断](./04-barge-in.md)

## 1. 阶段目标

播放能力围绕 PlaybackManager 设计。第一阶段先支持可播放音频资源，后续再扩展为低延迟音频流：

```text
resource playback:
  Hermes text -> TTSEngine -> audio resource -> PlaybackManager -> 音箱播放

stream playback:
  Hermes text -> TTSEngine -> PCM chunks -> PlaybackManager -> 音箱播放
```

长期目标是让 PlaybackManager 掌握播放开始、结束、中断和参考音频。

## 2. 音频资源播放模式

音频资源播放模式用于最小闭环：

```text
HermesResponse.text
  -> TTSEngine.synthesize_file()
  -> PlaybackResource(url=..., format=...)
  -> PlaybackManager
  -> PlayAudioResourceCommand
```

优点：

- 实现简单。
- 适合快速建立本地 ASR、Hermes、TTS 和播放闭环。
- gateway 可以记录播放 session、音频资源和播放结果。

缺点：

- 首播延迟通常高于流式播放。
- 播放进度粒度较粗。
- 对自然打断和参考音频的支持有限。

## 3. 音频流播放模式

音频流播放模式是低延迟目标：

```text
HermesResponse.text
  -> TTSEngine.synthesize_stream()
  -> PCM chunks
  -> PlaybackManager
  -> Stream(tag="play")
  -> 音箱 aplay
```

音箱端需要：

```text
RPC start_play
Stream(tag="play", bytes=pcm)
RPC stop_play
```

## 4. PlaybackManager

职责：

- 接收待播放文本或音频。
- 管理当前播放 session。
- 启动和停止音箱播放。
- 流式发送音频块。
- 发布播放生命周期事件。
- 在打断时取消旧播放。
- 保存当前播放参考音频，供后续回声抑制使用。

播放事件：

```text
PlaybackStarted
PlaybackChunkSent
PlaybackFinished
PlaybackStopped
PlaybackFailed
```

## 5. TTS Engine

TTS 通过接口隔离：

```python
class TTSEngine:
    async def synthesize_stream(self, text: str) -> AsyncIterator[AudioChunk]: ...
    async def synthesize_file(self, text: str) -> PlaybackResource: ...
```

第一版可以先不固定 TTS 模型。设计上只要求输出可由音箱播放的音频资源；流式播放阶段再要求输出符合音箱 `aplay` 配置的 PCM，或由 PlaybackManager 统一重采样。

## 6. 播放与状态机

状态机关系：

```text
THINKING
  -> SPEAKING          on PlaybackStarted

SPEAKING
  -> FOLLOWUP_WAIT     on PlaybackFinished
  -> INTERRUPTED       on BargeInConfirmed
```

如果播放失败：

```text
SPEAKING
  -> ERROR_RECOVERY
```

## 7. 与自然打断的关系

自然打断依赖可控播放：

- 如果走音频资源播放，应通过 PlaybackManager 停止当前 playback，并废弃后续播放事件。
- 如果走音频流，可以立即停止发送音频并调用 `stop_play`。
- gateway 生成或代理的音频可以作为回声抑制参考。

打断设计见 [04 自然打断](./04-barge-in.md)。

## 8. 播放策略

建议策略：

- 短回答优先直接播放。
- 长回答应限制长度，或支持分段播放。
- 播放前记录 conversation_id、turn_id、playback_id。
- 新一轮用户插话时，旧 playback_id 下的所有音频块都应作废。
