# 03 连续对话

本文定义在最小闭环之后，如何加入免唤醒连续追问能力。

上级索引：[Voice Gateway 总设计](./DESIGN.md)  
前置文档：[02 最小闭环](./02-minimal-loop.md)  
后续文档：[04 自然打断](./04-barge-in.md)

## 1. 阶段目标

回答结束后不立即退出会话，而是进入一个短暂的继续监听窗口：

```text
用户首轮唤醒
  -> 提问
  -> Hermes 回答
  -> FOLLOWUP_WAIT
  -> 用户继续说
  -> 新一轮 ASR / Hermes
```

连续对话不等于底层音频一直录。底层音频可以持续存在，但业务层必须明确维护会话窗口、轮次和超时。

## 2. 新增状态

在最小闭环基础上新增：

```text
FOLLOWUP_WAIT
```

状态机变为：

```text
SPEAKING
  -> FOLLOWUP_WAIT      on PlaybackFinished

FOLLOWUP_WAIT
  -> ENDPOINTING        on SpeechStarted
  -> IDLE               on FollowupTimeout
```

## 3. 会话窗口

`FOLLOWUP_WAIT` 是免唤醒窗口。

建议策略：

```text
followup_timeout_ms: 15000-30000
min_followup_speech_ms: 250-400
max_idle_noise_ms: ignore
```

在这个窗口内，用户不需要再次说唤醒词。

## 4. Conversation 与 Turn

连续对话必须引入 conversation：

```python
Conversation:
    conversation_id: str
    turns: list[Turn]
    state: DialogueState
    active_speaker_id: str | None

Turn:
    turn_id: str
    asr: ASRResult
    hermes_response: HermesResponse | None
```

首轮创建 conversation。后续追问复用同一个 conversation，直到：

- follow-up 超时。
- 用户明确说“结束”“不用了”。
- 发生不可恢复错误。
- 设备断开。

## 5. Hermes 上下文

HermesConnector 在连续对话阶段需要传入历史：

```python
HermesTurn:
    conversation_id: str
    user_text: str
    history: list[DialogueMessage]
```

历史不应该无限增长。建议策略：

- 保存最近 N 轮短上下文。
- 长对话由 Hermes 自身记忆或摘要系统承接。
- TTS 文本不必完整进入下一轮，只保留语义摘要或最终回答。

## 6. VAD 策略变化

`FOLLOWUP_WAIT` 中 VAD 目标是检测用户是否继续说话。

与首轮唤醒不同：

- 不需要 KWS。
- 阈值可以略低，提高追问灵敏度。
- 仍需防止环境噪声误触发。

## 7. 退出策略

退出 `FOLLOWUP_WAIT` 的条件：

```text
超时无人说话
用户说结束词
连续 ASR 空结果
设备断开
系统进入 ERROR_RECOVERY
```

结束词示例：

```text
好了
不用了
结束
先这样
```

## 8. 与后续能力的关系

连续对话是自然打断的基础，但不包含播放中插话。

- 本阶段只处理“回答结束后继续说”。
- 播放中插话由 [04 自然打断](./04-barge-in.md) 处理。
- 说话人身份由 [05 声纹识别](./05-speaker-identity.md) 注入。

