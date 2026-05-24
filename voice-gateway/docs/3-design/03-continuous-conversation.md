# 03 连续对话

本文定义在最小闭环之后，如何加入免唤醒连续追问能力。

上级索引：[Voice Gateway 总设计](./DESIGN.md)  
前置文档：[02 最小闭环](./02-minimal-loop.md)  
后续文档：[04 自然打断](./04-barge-in.md)

## 0. 实现状态

状态：首版已实现，等待真实音箱体验调参。

前置条件已经满足：

- `02 最小闭环` 已完成真实音箱链路。
- 当前首轮回答结束后会进入 `FOLLOWUP_WAIT`，超时后回到 `WAIT_WAKE_WORD` / `IDLE`。
- Hermes request/response 文本已经进入结构化日志，便于验证多轮上下文。
- PlaybackManager 已经能感知播放完成或失败。

本阶段没有重做唤醒、ASR、Hermes 或 TTS，而是在现有最小闭环之上增加了会话窗口和历史传递。

已落地能力：

- `DialogueState.FOLLOWUP_WAIT`。
- `XiaoAIMinimalRuntime.FOLLOWUP_WAIT`。
- 默认 `VOICE_GATEWAY_FOLLOWUP_TIMEOUT_SECONDS=15`。
- 追问复用同一个 `conversation_id`。
- Hermes 请求携带最近 10 轮 `DialogueMessage` 历史。
- 结构化日志包含 `followup.started`、`followup.timeout` 和 `history_turns`。

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

本阶段的最小可交付版本：

```text
首轮：你好 -> 问题 -> Hermes 回答 -> 音箱播放
播放结束后进入 FOLLOWUP_WAIT
用户在窗口内直接追问，不需要再说“你好”
追问进入同一个 conversation，并带最近历史发给 Hermes
窗口超时后回到 WAIT_WAKE_WORD / IDLE
```

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
  -> LISTENING          on FollowupSpeechStarted
  -> ENDPOINTING        on SpeechStarted
  -> IDLE               on FollowupTimeout
```

落到当前代码时，建议拆成：

```text
XiaoAIMinimalRuntime:
  WAIT_WAKE_WORD
  WAIT_QUESTION
  FOLLOWUP_WAIT

MinimalLoopGateway:
  IDLE
  LISTENING
  ENDPOINTING
  THINKING
  SPEAKING
  FOLLOWUP_WAIT
```

`FOLLOWUP_WAIT` 期间不走 wake ASR；音频直接进入 question endpoint / question ASR。

## 3. 会话窗口

`FOLLOWUP_WAIT` 是免唤醒窗口。

建议策略：

```text
followup_timeout_ms: 15000-30000
min_followup_speech_ms: 250-400
max_idle_noise_ms: ignore
```

在这个窗口内，用户不需要再次说唤醒词。

首版默认值建议：

```text
VOICE_GATEWAY_FOLLOWUP_TIMEOUT_SECONDS=15
```

后续可以根据实测误触发率调整到 20-30 秒。

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

首版可以先不引入完整 Conversation 对象仓储，先在 `MinimalLoopGateway` 内维护：

```python
conversation_id: str | None
turn_id: str | None
history: list[DialogueMessage]
```

其中 `history` 只保存最近 10 轮的用户文本和 Hermes 回答文本。

## 5. Hermes 上下文

HermesConnector 在连续对话阶段需要传入历史：

```python
HermesTurn:
    conversation_id: str
    user_text: str
    history: list[DialogueMessage]
```

历史不应该无限增长。建议策略：

- 保存最近 N 轮短上下文，首版 N=10。
- 长对话由 Hermes 自身记忆或摘要系统承接。
- TTS 文本不必完整进入下一轮，只保留语义摘要或最终回答。

首版历史格式建议：

```python
DialogueMessage(role="user", content=user_text)
DialogueMessage(role="assistant", content=response_text)
```

Hermes 请求日志需要继续打印本轮 `user_text`，并额外打印或统计 `history_turns`，避免日志过长。

## 6. VAD 策略变化

`FOLLOWUP_WAIT` 中 VAD 目标是检测用户是否继续说话。

与首轮唤醒不同：

- 不需要 KWS。
- 阈值可以略低，提高追问灵敏度。
- 仍需防止环境噪声误触发。
- 需要忽略刚播完的 TTS 尾音，避免把音箱自己的回答尾巴当作追问。

首版可以复用现有 question endpoint，先不引入单独 follow-up VAD 参数；如果实测误触发多，再拆 `FOLLOWUP_WAIT` 专用阈值。

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

首版可先只实现超时退出；结束词作为第二个小步，避免和 ASR 误识别调参混在一起。

## 8. 实现拆分

首版已按下面顺序落地：

1. 在 `DialogueState` 增加 `FOLLOWUP_WAIT`，让 `MinimalLoopGateway` 播放完成后不立即清空 conversation。
2. 增加 `followup_timeout_seconds` 配置，默认 15 秒。
3. 在 `XiaoAIMinimalRuntime` 增加 `FOLLOWUP_WAIT`，播放完成后进入该状态。
4. `FOLLOWUP_WAIT` 期间收到语音直接进入 question ASR / Hermes，不要求唤醒词。
5. 在 `MinimalLoopGateway` 保存最近 N 轮历史，并传给 HermesConnector。
6. 日志补充 `conversation_id`、`turn_id`、`history_turns`、`followup.timeout`、`followup.started`。
7. 补单元测试：首轮后追问复用 conversation，超时回到待唤醒。

当前验收标准：

- 用户说“你好”完成首轮问答后，可以在 15 秒内直接追问。
- 追问不需要再次说“你好”。
- Hermes 能收到本轮问题和最近历史。
- 超时后系统回到待唤醒。
- 播放失败、ASR 空结果、Hermes 失败仍能恢复。

## 9. 与后续能力的关系

连续对话是自然打断的基础，但不包含播放中插话。

- 本阶段只处理“回答结束后继续说”。
- 播放中插话由 [04 自然打断](./04-barge-in.md) 处理。
- 说话人身份由 [05 声纹识别](./05-speaker-identity.md) 注入。
