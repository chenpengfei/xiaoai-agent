# 04 自然打断

本文定义回答播放过程中，用户插话打断当前回答的设计。

上级索引：[Voice Gateway 总设计](./DESIGN.md)  
前置文档：[03 连续对话](./03-continuous-conversation.md)  
相关文档：[06 TTS 与播放控制](./06-tts-playback.md)

## 1. 阶段目标

当助手正在播放回答时，用户可以直接说话打断：

```text
SPEAKING
  -> 用户插话
  -> 停止当前播放
  -> 进入新一轮 LISTENING / ENDPOINTING
```

自然打断的核心不是 ASR，而是 VAD、播放状态、播放参考音频和对话状态共同决策。

## 2. 新增状态

新增：

```text
INTERRUPTED
```

状态转移：

```text
SPEAKING
  -> INTERRUPTED        on BargeInConfirmed

INTERRUPTED
  -> LISTENING          after PlaybackStopped
```

## 3. 打断输入

判断打断需要这些信号：

- 当前状态是 `SPEAKING`。
- PlaybackManager 知道正在播放回答。
- VAD 检测到持续人声。
- 可选声纹确认不是设备自身回声。
- 可选播放参考音频用于回声抑制。

## 4. 第一版策略

第一版不要求完整 AEC，但需要避免太敏感。

```text
if state == SPEAKING
and playback_active == true
and elapsed_since_playback_start > barge_in_grace_ms
and speech_probability 持续高于阈值超过 barge_in_min_speech_ms
then BargeInConfirmed
```

建议参数：

```text
barge_in_grace_ms: 800-1500
barge_in_min_speech_ms: 300-600
speaking_vad_threshold: 高于普通 LISTENING 阈值
```

## 5. 打断动作

确认打断后：

```text
1. PlaybackManager 停止当前播放。
2. 取消当前 TTS 流。
3. 标记当前 assistant turn 为 interrupted。
4. 清理过期 Hermes 响应。
5. 状态进入 LISTENING。
```

如果当前播放走音频资源模式，应由 PlaybackManager 停止当前 playback，并废弃过期播放事件。  
如果播放走音频流模式，则直接停止 `Stream(tag="play")` 并发送 `stop_play`。

## 6. 回声问题

播放中麦克风会收到音箱自己的声音。没有处理时，VAD 可能把 TTS 当成用户插话。

分阶段处理：

```text
第一版：
  提高 SPEAKING 状态下 VAD 阈值
  设置 barge_in_grace_ms
  要求连续人声超过最小时长

第二版：
  PlaybackManager 保存当前播放参考音频
  做简单相关性/能量判断

第三版：
  引入 AEC 或更强的回声抑制
```

## 7. 显式打断词

当 ASR 能在播放中给出部分文本时，可支持显式打断词：

```text
停一下
等一下
不用说了
打住
换个问题
```

显式打断可以降低 VAD 误判，但不能替代 VAD，因为 ASR 结果通常比打断判断晚。

## 8. 与播放设计的关系

自然打断长期依赖可控播放。  
如果使用音频资源播放，gateway 至少需要掌握播放 session、开始、结束、停止和失败事件。  
如果使用音频流播放，gateway 可以更自然地取消播放并保留参考音频。

播放细节见 [06 TTS 与播放控制](./06-tts-playback.md)。
