# 03 连续对话

本文定义在最小闭环之后，如何加入免唤醒连续追问能力。

上级索引：[Voice Gateway 总设计](./DESIGN.md)  
前置文档：[02 最小闭环](./02-minimal-loop.md)  
后续文档：[04 自然打断](./04-barge-in.md)

## 0. 实现状态

状态：首版已实现，但需要按本文的目标架构收束。

前置条件已经满足：

- `02 最小闭环` 已完成真实音箱链路。
- 当前首轮回答结束后会进入 `FOLLOWUP_WAIT`，超时后回到 `WAIT_WAKE_WORD` / `IDLE`。
- Hermes request/response 文本已经进入结构化日志，便于验证多轮上下文。
- PlaybackManager 已经能感知播放完成或失败。

本阶段没有重做唤醒、ASR、Hermes 或 TTS，而是在现有最小闭环之上增加了会话窗口和历史传递。
当前代码已经解决了几个真实问题，但实现仍带有增量补丁痕迹。后续修改应优先把策略收束到明确组件里，而不是继续在 runtime worker 里增加条件分支。

已落地能力：

- `DialogueState.FOLLOWUP_WAIT`。
- `XiaoAIMinimalRuntime.FOLLOWUP_WAIT`。
- 默认 `VOICE_GATEWAY_FOLLOWUP_TIMEOUT_SECONDS=15`。
- 追问复用同一个 `conversation_id`。
- Hermes 请求携带最近 10 轮 `DialogueMessage` 历史。
- 结构化日志包含 `followup.started`、`followup.timeout` 和 `history_turns`。
- 问题 ASR 捕获后先进入 800ms 合并窗口，窗口内如果 VAD 又切出后续短段，则合并文本后再送 Hermes。
- Runtime 不再在首个问题段完成后无条件清空音频队列，避免把同一句话后半段丢掉。

## 1. 设计目标

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

目标设计只解决三件事：

```text
1. 什么时候可以开始采集用户问题。
2. 如何判断一个用户问题已经完整。
3. 如何把完整问题作为一个 turn 交给对话层。
```

其它能力都不能混进这三件事里：

- ACK 播放是唤醒反馈，不是问题采集。
- 回答播放后的回声保护是输入门控，不是 ASR 文本修补。
- 多段合并是 turn assembly，不是 Hermes 上下文能力。
- Conversation/history 是对话层能力，不应该反向影响音频采集。

整体设计分成四层：

```text
Audio Ingress
  持续接收音箱 PCM chunk，只负责排队、限流、丢弃过期音频。

Capture Policy
  决定当前 chunk 该进入 wake VAD、question VAD，还是被忽略。
  管理 ACK 后句首保护、回答播放后的回声保护、follow-up 窗口。

Turn Assembly
  负责把一次或多次 VAD speech_ended 得到的 ASR 文本组装成一个用户 turn。
  这里处理 ACK 前缀剥离、过短文本保护、merge window。

Dialogue Orchestration
  一旦 turn 文本确定，才调用 Hermes、TTS/playback，并维护 conversation/history。
```

也就是说，连续对话的核心不是“回答后继续把音频送给 ASR”，而是：

```text
先判断现在能不能接受一个新问题
再判断用户问题是否已经完整
最后才把完整 turn 交给对话层
```

本阶段的最小可交付版本：

```text
首轮：你好 -> 问题 -> Hermes 回答 -> 音箱播放
播放结束后进入 FOLLOWUP_WAIT
用户在窗口内直接追问，不需要再说“你好”
追问进入同一个 conversation，并带最近历史发给 Hermes
窗口超时后回到 WAIT_WAKE_WORD / IDLE
```

## 2. 目标架构

连续对话应以一个主状态机驱动，而不是由多个局部布尔值互相暗示。

推荐的主流程：

```text
IDLE
  -> WAKE_LISTENING
  -> ACKING
  -> WAITING_FOR_USER_SPEECH
  -> CAPTURING_UTTERANCE
  -> ASSEMBLING_TURN
  -> ANSWERING
  -> PLAYING
  -> FOLLOWUP_WAIT
  -> WAITING_FOR_USER_SPEECH
```

其中：

- `WAITING_FOR_USER_SPEECH` 表示允许用户开始说话，但尚未确认有人声。
- `CAPTURING_UTTERANCE` 表示 VAD 已确认人声开始，正在收集音频。
- `ASSEMBLING_TURN` 表示已经得到一个或多个 ASR 文本片段，正在等待可能的同句后续片段。
- `ANSWERING` 表示 turn 文本已确定，正在调用 Hermes / TTS / playback。
- `FOLLOWUP_WAIT` 表示回答已播完，conversation 暂时保留，但尚未开始下一轮采集。

这个流程有一个硬约束：

```text
收到 PCM chunk 本身不能触发 follow-up turn。
只有 VAD 确认用户开始说话，才能从 FOLLOWUP_WAIT 进入 CAPTURING_UTTERANCE。
```

如果代码上为了复用组件需要提前初始化 VAD，也不能提前创建新 turn，也不能提前清掉 follow-up timeout。

## 2.1 组件边界

目标代码结构应包含这些清晰组件：

```text
AudioQueue
  输入 PCM chunk。
  支持按时间丢弃过期音频、清理播放期间积压音频。
  不理解 wake、ASR、Hermes。

InputGate
  根据当前状态决定 chunk 是 accepted、ignored 还是 drained。
  包含 ACK 后策略、post-playback 策略、follow-up timeout。
  不做 ASR 文本处理。

UtteranceCapture
  封装 VAD + final ASR。
  输入 accepted chunk，输出 SpeechStarted / SegmentCaptured / CaptureTimeout。
  不调用 Hermes。

TurnAssembler
  输入 ASR segment text。
  执行 ACK 前缀过滤、短文本保护、continuation window。
  输出 TurnReady / KeepWaiting / Ignored。

DialogueSession
  输入 TurnReady。
  调用 Hermes、TTS/playback，维护 conversation_id 和 history。
  不直接读取麦克风队列。
```

这样每个问题都有唯一归属：

```text
丢不丢音频       -> InputGate
一句话切没切完   -> UtteranceCapture + TurnAssembler
要不要继续追问   -> DialogueSession 返回后的主状态机
上下文怎么传     -> DialogueSession
```

## 2.2 策略原则

所有策略都要满足下面原则：

- 状态转移必须由领域事件触发，例如 `WakeDetected`、`SpeechStarted`、`SegmentCaptured`、`TurnReady`、`PlaybackFinished`、`FollowupTimeout`。
- 参数必须属于明确策略，例如 `post_playback_ignore_seconds` 属于 `InputGate`，`merge_window_seconds` 属于 `TurnAssembler`。
- 不用“看起来像某个答案”的文本规则来判断回声；回声优先用播放状态、时间窗口、队列边界、后续 AEC 解决。
- 不在 Hermes prompt 里修复采集问题；采集不完整就不要送 Hermes。
- 不让 ACK、防回声、连续追问三件事互相复用同一段隐式逻辑。

## 3. 状态设计

在最小闭环基础上新增：

```text
FOLLOWUP_WAIT
```

抽象状态机：

```text
SPEAKING
  -> FOLLOWUP_WAIT      on PlaybackFinished

FOLLOWUP_WAIT
  -> LISTENING          on FollowupSpeechStarted
  -> ENDPOINTING        on SpeechStarted
  -> IDLE               on FollowupTimeout

ENDPOINTING
  -> THINKING           on SpeechEnded

THINKING
  -> LISTENING          on QuestionCaptured / MergeWait

LISTENING
  -> THINKING           on MergeWindowElapsed
```

当前代码里有两套状态：

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

当前分工应重新界定：

- `XiaoAIMinimalRuntime` 暂时管音频入口和采集策略。
- `MinimalLoopGateway` 暂时管一轮对话的业务状态和 history。
- 后续应收束为一个主状态机，`MinimalLoopGateway` 退化为 DialogueSession 服务，避免两套状态互相推断。

在收束前，这两套状态必须保持下面分工：

```text
RuntimeState.WAIT_WAKE_WORD
  -> 只跑 wake endpoint / wake ASR

RuntimeState.WAIT_QUESTION
  -> 已经确认本轮要采集用户问题
  -> 跑 question endpoint / question ASR

RuntimeState.FOLLOWUP_WAIT
  -> 已经完成一轮回答，conversation 暂时保留
  -> 等待用户在窗口内开始追问
```

`MinimalLoopGateway` 不应该决定什么时候丢弃麦克风音频，也不应该知道 ACK 过滤、post-playback ignore 这类采集策略；它只应该接收一个确定的 `Turn`，然后完成 Hermes/TTS/playback。

理想状态下，`FOLLOWUP_WAIT` 到 `LISTENING` 的转换应由“VAD 确认用户开始说话”触发，而不是由“收到任意 PCM chunk”触发。当前实现为了复用 question endpoint，会在追问窗口收到 chunk 后立即创建 follow-up turn，这属于首版简化，后续应收束成显式的 follow-up capture phase。

## 4. 当前实现审查结论

当前实现已经覆盖了主要真实问题：

- ACK 后不额外静音，避免“唤醒后立即说话”丢句首。
- ACK 短句通过文本前缀过滤剥离或忽略。
- 问题被 VAD 切成多段时，用 800ms merge window 合并。
- 回答播放后清空播放期间积压音频，并用 600ms post-playback ignore 避免 TTS 回声触发追问。
- 回答结束后保留 conversation/history，使追问能带上下文。

但代码里仍有几个需要尽快收束的结构风险：

```text
1. FOLLOWUP_WAIT 收到任意 chunk 就 begin_followup_turn
   这会把“等待用户开始说话”和“正在采集问题”混成一个阶段。
   如果只是环境噪声或静音 chunk，可能提前创建 turn，并影响 follow-up timeout 语义。

2. merge window 是 runtime worker 的局部变量
   pending_turn / merge_deadline 目前不属于显式状态对象。
   后续一旦加入取消、打断、空 ASR 容错，会让 worker 分支继续变复杂。

3. capture_audio 既做 ASR 捕获，又会在 ignored/failed 时结束 conversation
   这让 Turn Assembly 和 Dialogue Orchestration 边界不够干净。
   更好的做法是 capture 只返回 captured/ignored/failed result，由 runtime 决定继续等、合并、还是结束。

4. ACK 文本过滤通过 runtime 注入 gateway.asr_text_transform
   这是有效但隐式的耦合。
   后续应抽成独立 QuestionTextFilter / TurnNormalizer，并作为 capture policy 显式传入。
```

这些问题不应该继续用局部补丁修。下一步应按 `InputGate -> UtteranceCapture -> TurnAssembler -> DialogueSession` 的边界重构。

## 5. 会话窗口

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

## 6. Conversation 与 Turn

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

## 7. Hermes 上下文

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

## 8. VAD 策略变化

`FOLLOWUP_WAIT` 中 VAD 目标是检测用户是否继续说话。

与首轮唤醒不同：

- 不需要 KWS。
- 阈值可以略低，提高追问灵敏度。
- 仍需防止环境噪声误触发。
- 需要忽略刚播完的 TTS 尾音，避免把音箱自己的回答尾巴当作追问。

首版可以复用现有 question endpoint 参数，但不能复用“收到 chunk 就开始 turn”的行为。正确边界是：follow-up 窗口内先让 VAD 观察音频，只有 `SpeechStarted` 后才创建新 turn。

## 8.1 回答播放后的回声保护

回答播放结束后，音箱麦克风里可能还残留刚播出的 TTS 尾音，或者 runtime 队列里已经积压了播放期间回采到的音频。如果此时立即进入 `FOLLOWUP_WAIT` 并消费队列，系统会把自己的回答识别成用户追问，形成“自己问自己、自己答自己”的循环。

策略：

```text
answer playback finished
  -> 清空播放期间积压的 runtime 音频队列
  -> 进入短暂 post-playback ignore window
  -> ignore window 结束后才接受免唤醒追问音频
```

参数：

```text
VOICE_GATEWAY_POST_PLAYBACK_IGNORE_SECONDS=0.6
```

约束：

- 该保护只作用在回答播放完成后、进入追问窗口前后。
- 不作用在唤醒 ACK 后；ACK 后仍保持 `VOICE_GATEWAY_ACK_SUPPRESSION_SECONDS=0`，避免快语速用户的句首被切掉。
- 队列清理发生在一轮问题已经送 Hermes 并完成回答播放之后，不影响问题段合并窗口。
- 如果后续引入真正的 playback finished 事件或 AEC，可用更精确的播放尾音判定替代固定窗口。

这是一个输入门控策略：播放状态结束后，麦克风输入在很短时间内仍可能包含设备自身声音。它的归属必须是 `InputGate`，不能散落在 ASR 文本过滤里。

## 8.2 问题段合并策略

真实音频中，用户一句话内部可能有很短停顿，VAD 会把它切成两个 `speech_ended`。例如“有用的人没有一个不累的”可能先被切出“有用的人”，后半句随后到达。如果首段 ASR 完成后立即送 Hermes，并清空 runtime 队列，后半句会被丢弃，最终只识别到前半段。

策略：

```text
speech_ended -> ASR 捕获文本
  -> 进入 merge window
  -> 800ms 内有新的 speech_ended：拼接文本，重新等待 800ms
  -> 窗口结束：一次性送 Hermes / TTS
```

参数：

```text
VOICE_GATEWAY_MERGE_WINDOW_SECONDS=0.8
```

约束：

- 合并发生在 ASR 文本捕获之后、Hermes 请求之前。
- 合并窗口内继续保留 runtime 队列，不做无条件 drain。
- 只合并已经通过 ACK 前缀过滤后的有效问题文本。
- ACK 短句过滤命中 `ignore_short` 时仍继续等待用户正式问题，不进入 Hermes。
- 合并后的文本按中文连续文本直接拼接，例如 `有用的人` + `没有一个不累的` -> `有用的人没有一个不累的`。

这也不是文本修补，而是 turn assembly 的 continuation timeout：VAD 已经给出了一个候选片段，但系统允许同一句话的后续片段在短窗口内补齐。

## 9. 退出策略

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

## 10. 重构路线

不要继续围绕单个现象小步补丁。推荐按下面顺序做一次结构化收束：

1. 引入 `TurnAssembler`，把 ACK 前缀过滤、短文本保护、merge window 从 runtime worker 中拿出来。
2. 引入 `InputGate`，把 ACK 后策略、post-playback ignore、queue drain、follow-up timeout 统一管理。
3. 改造 follow-up：`FOLLOWUP_WAIT` 只喂 VAD，不创建 turn；等 `SpeechStarted` 后再创建 turn 并进入 capture。
4. 让 `MinimalLoopGateway.capture_audio()` 不再在 ignored/failed 时自行结束 conversation，而是返回结构化 capture result。
5. 将 `MinimalLoopGateway` 逐步收束为 `DialogueSession.answer(turn)`，只负责 Hermes/TTS/playback/history。
6. 为每个阶段补领域事件日志：`input_gate.ignored`、`utterance.started`、`segment.captured`、`turn_assembly.waiting`、`turn.ready`、`followup.timeout`。

当前验收标准：

- 用户说“你好”完成首轮问答后，可以在 15 秒内直接追问。
- 追问不需要再次说“你好”。
- Hermes 能收到本轮问题和最近历史。
- 超时后系统回到待唤醒。
- 播放失败、ASR 空结果、Hermes 失败仍能恢复。
- follow-up 窗口中的静音/噪声 chunk 不会创建新 turn。
- ACK、防回声、问题合并分别有独立单测，不互相依赖。

## 11. 与后续能力的关系

连续对话是自然打断的基础，但不包含播放中插话。

- 本阶段只处理“回答结束后继续说”。
- 播放中插话由 [04 自然打断](./04-barge-in.md) 处理。
- 说话人身份由 [05 声纹识别](./05-speaker-identity.md) 注入。
