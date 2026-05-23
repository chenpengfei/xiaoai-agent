# Voice Gateway 开发路线

本文是 `voice-gateway` 文档的根索引。读者应该从这里开始，再按 `1-idea -> 2-poc -> 3-design` 的顺序下钻阅读；最小闭环跑通后的运维设计在 `4-ops`。

这条路线的核心是：

```text
1-idea
  -> 2-poc
  -> 3-design
  -> 4-ops
  -> implementation
```

其中：

- [1-idea](./1-idea/README.md) 记录最原始的想法、判断和探索思路。
- [2-poc](./2-poc/README.md) 负责完成可行性验证，证明关键链路能跑通。
- [3-design](./3-design/DESIGN.md) 负责正向设计，指导工程实现。
- [4-ops](./4-ops/README.md) 负责最小闭环后的日志、监控、告警和排障设计。

目录名中的 `1-`、`2-`、`3-`、`4-` 表示工程开展顺序，也表示读者的推荐阅读顺序。

## 1. 文档分层

### 1.1 1-idea：原始想法

[`1-idea`](./1-idea/README.md) 目录保存最早期的理解和路线判断。

它的作用不是约束最终实现，而是记录最初为什么要做这件事，包括：

- 家里为什么需要一个更聪明的高频语音入口。
- 为什么选择改造小爱音箱，而不是另起一个入口。
- 为什么希望接入 Mac Mini 上的 Agent。
- 希望逐步引入哪些更接近人类对话的能力。
- 长期想把它演进成怎样的家庭智能管家。

`1-idea` 中的内容允许粗糙、允许带有探索痕迹，也允许随着后续验证被修正。

### 1.2 2-poc：可行性验证

[`2-poc`](./2-poc/README.md) 目录负责回答：

```text
这件事能不能做？
关键链路能不能跑通？
有哪些现实限制？
```

PoC 的目标是 `make it possible`。

它可以使用临时脚本、开发态命令、局部 hack 和较粗的集成方式，只要能可靠证明某条链路可行即可。

当前 PoC 已验证过的方向包括：

- 音箱刷写补丁后可以运行 open-xiaoai client。
- 音箱可以连接 Mac Mini 上的 server。
- Mac Mini 可以调用 Hermes。
- 3-* legacy route 可以通过小米云 ASR 文本触发 Hermes，并作为手动回滚方案保留。
- 音箱本地 KWS / VAD 可以作为本地语音入口。
- 音箱 record stream 可以把 PCM 音频传到 Mac Mini。
- Mac Mini 可以用 sherpa-onnx 做本地 VAD / STT。
- Mac Mini 可以生成 TTS URL，并让音箱播放。
- 本地 Hermes 链路具备完全接管音箱语音问答的可行性。

### 1.3 3-design：正向开发设计

[`3-design`](./3-design/DESIGN.md) 目录负责回答：

```text
应该怎么把 PoC 变成稳定、可维护、可演进的系统？
每一步交付什么？
每一步怎么验收？
```

Design 的目标不是继续证明“能不能做”，而是指导 `voice-gateway` 的正向开发。

设计原则：

- 每一步只新增一个主要能力。
- 每一步结束时都必须可运行、可验证。
- 每一步都有明确验收标准。
- 不把多个大能力揉进一个超长任务。
- 先建立稳定闭环，再逐步丰富体验。
- 先保证行为正确，再优化速度，最后收口安全和隐私。

## 2. 总体开发节奏

整体节奏分成四段：

```text
Step 1      make run
Step 2-7    make right
Step 8      make fast
Step 9      security & privacy
```

这不是口号，而是任务拆分原则。

## 3. Step 1：make run

Step 1 只解决一件事：

```text
让 voice-gateway 可以通过 make run 启动，并跑通最小闭环。
```

最小闭环：

```text
小爱音箱 PCM 音频
  -> Mac Mini voice-gateway
  -> VAD / Endpointing
  -> ASR
  -> Hermes
  -> TTSEngine
  -> PlaybackResource
  -> PlaybackManager
  -> 小爱音箱播报
```

这一阶段不追求完整体验。

明确不做：

- 连续对话。
- 自然打断。
- 声纹识别。
- 复杂播放控制。
- 完整观测系统。
- 性能优化。
- 安全策略收口。

Step 1 的目标是把系统从零散 PoC 变成一个可启动、可运行、可验证的服务。

验收标准示例：

- `make run` 可以启动 gateway。
- 音箱可以连接 gateway。
- gateway 可以接收音箱 PCM 音频。
- VAD 可以切出一次用户 utterance。
- ASR 可以输出可用文本。
- Hermes 可以返回短回答。
- 音箱可以播报回答。
- 一次请求结束后状态回到 `IDLE`。
- 失败不会导致进程崩溃。

## 4. Step 2-7：make right

Step 2 到 Step 7 的目标是逐步把能力做正确。

这一阶段每一步都应该是小而完整的能力增量。每个步骤结束时，系统都应该处于可运行状态，而不是半成品状态。

建议展开方式：

```text
Step 2：连续对话
Step 3：自然打断
Step 4：声纹识别
Step 5：TTS 与播放控制
Step 6：对话状态机和错误恢复完善
Step 7：多轮上下文、失败反馈和体验收口
```

具体顺序可以随着实现情况微调，但原则不变：

- 每一步只引入一个主要复杂度来源。
- 新能力必须接入状态机。
- 新能力必须有日志。
- 新能力必须有测试用例或手工验证 runbook。
- 新能力失败时必须能回到稳定状态。

### 4.1 连续对话

目标：

```text
回答结束后进入 FOLLOWUP_WAIT，用户可以免唤醒继续追问。
```

关键点：

- 引入 `Conversation` 和 `Turn`。
- 回答结束后不立即回到 `IDLE`。
- follow-up 超时后自动退出。
- 用户说“结束”“不用了”等结束词时退出。
- 历史上下文长度可控。

验收标准示例：

- 首轮问题可以正常回答。
- 回答结束后进入 follow-up 窗口。
- 用户继续说话可以进入下一轮。
- follow-up 超时后回到 `IDLE`。
- 连续两轮不会串 session。

### 4.2 自然打断

目标：

```text
助手播放回答时，用户可以直接插话打断。
```

关键点：

- 在 `SPEAKING` 状态继续监听 VAD。
- 设置播放起始保护时间，避免误判。
- 要求持续人声超过阈值后才确认打断。
- 打断后取消旧 TTS、旧播放和过期 Hermes 响应。
- 状态进入新一轮监听。

验收标准示例：

- 播放中说“停一下”可以停止当前播放。
- 播放刚开始的短暂回声不会误触发打断。
- 打断后可以继续提出新问题。
- 旧回答不会在新问题后继续播放。

### 4.3 声纹识别

目标：

```text
为每轮用户语音附加 SpeakerIdentity。
```

关键点：

- 声纹识别不阻塞普通问答。
- 识别结果分为 `identified`、`unknown`、`ambiguous`。
- 不把原始 embedding 发送给 Hermes。
- 声纹 profile 和普通日志分开存储。
- 身份用于个性化记忆和权限判断。

验收标准示例：

- 已注册成员可以被识别。
- 未知说话人可以继续普通问答。
- ambiguous 状态不会执行高权限动作。
- 声纹失败时本轮问答仍可完成。

### 4.4 TTS 与播放控制

目标：

```text
设计 gateway 管理的 TTS、音频资源播放、播放生命周期和低延迟音频流接口。
```

关键点：

- 引入 `TTSEngine` 接口。
- 引入 `PlaybackManager`。
- 播放生命周期事件结构化。
- 支持取消当前 playback。
- 保存播放参考音频，为后续回声抑制做准备。

验收标准示例：

- Mac Mini 可以生成可由音箱播放的音频资源。
- 音箱可以播放 gateway 生成或代理的音频资源。
- 播放开始、结束、失败都有事件。
- 新一轮打断会废弃旧 playback。

### 4.5 状态机和错误恢复完善

目标：

```text
让状态机覆盖正常路径、失败路径和恢复路径。
```

关键点：

- 明确 `IDLE`、`LISTENING`、`ENDPOINTING`、`THINKING`、`SPEAKING`、`FOLLOWUP_WAIT`、`INTERRUPTED`、`ERROR_RECOVERY` 的边界。
- 每个状态只接受合理事件。
- 超时、空 ASR、Hermes 失败、TTS 失败、播放失败都能恢复。
- 设备断开后清理旧 conversation、旧播放和监听状态。

验收标准示例：

- ASR 超时后回到 `IDLE`。
- Hermes 超时后可以播报简短失败反馈或安静失败。
- TTS 失败不会卡住状态机。
- 音箱断线重连后可以重新开始新 session。

### 4.6 多轮上下文、失败反馈和体验收口

目标：

```text
把多轮上下文、失败反馈策略和播报体验整理到可日常使用的状态。
```

关键点：

- 多轮历史长度可控。
- 长回答适合语音播报。
- 空问题有明确反馈。
- 失败路径的用户体验可接受。

验收标准示例：

- 连续追问可以带上必要上下文。
- 长回答会被压缩成适合播报的长度。
- 空 ASR 或空问题不会调用长链路。
- 失败反馈可用但不会掩盖主链路问题。

## 5. Step 8：make fast

Step 8 解决观测和性能优化。

这一阶段不再优先增加新能力，而是让系统变快、稳定、可诊断。

运维侧设计见：[4 Ops 运维设计](./4-ops/README.md)。

关注指标：

```text
audio.chunk.gap_ms
vad.latency_ms
asr.latency_ms
hermes.latency_ms
tts.latency_ms
playback.start_latency_ms
playback.duration_ms
barge_in.latency_ms
dialogue.turn.total_ms
error.count
```

关键工作：

- 统一 session、conversation、turn、playback 的 ID。
- 输出结构化日志。
- 记录端到端耗时。
- 找到 ASR、Hermes、TTS、播放中的主要耗时来源。
- 优化模型加载、音频缓冲、流式播放和并发任务。
- 为常见失败建立可查询日志。

验收标准示例：

- 每一轮请求都有完整 trace。
- 可以从日志中看出时间花在哪里。
- 连续请求没有明显资源泄漏。
- 常见失败可以定位到具体模块。
- 关键延迟指标有基线数据。

## 6. Step 9：security & privacy

Step 9 单独收口安全和隐私。

原因是 `voice-gateway` 同时处理家庭麦克风音频、声纹身份和音箱控制命令，不能只依赖“跑在局域网里”作为安全假设。

关键工作：

- gateway 默认只绑定可信网络。
- 音箱连接 gateway 前需要认证。
- `run_shell` 只能由 gateway 内部可信模块调用。
- 高权限动作需要明确权限策略和审计日志。
- 默认不长期保存原始音频。
- 调试音频保存必须显式开启。
- 声纹 profile 与普通日志分开管理。
- 日志中的用户原话需要保留周期和脱敏策略。
- 不把声纹 embedding 发给 Hermes 或第三方 LLM。
- `unknown` 和 `ambiguous` 身份不能执行敏感动作。

验收标准示例：

- 未认证连接不能控制音箱。
- 外部客户端不能直接调用 `run_shell`。
- 默认配置不会长期落盘原始音频。
- 声纹数据有独立存储位置和访问边界。
- 敏感动作会记录审计事件。
- 日志不会长期保存未脱敏的完整用户原话。

## 7. 每个步骤的完成定义

无论是哪一步，都必须满足统一完成定义：

- 有明确目标。
- 有清晰非目标。
- 有状态机或模块边界说明。
- 有可运行入口。
- 有手工或自动验证方法。
- 有成功标准。
- 有失败恢复策略。
- 不破坏前一步已完成能力。

如果一个步骤无法在合理时间内完成，应该继续拆小，而不是把多个未完成能力堆在同一个阶段里。

## 8. 总结

`voice-gateway` 的开发不是从零设计一个完美系统，而是从已经验证过的 PoC 出发，把可行链路逐步工程化。

最终目标是：

```text
PoC 证明这条路能走。
Design 把这条路拆成可交付步骤。
Implementation 按步骤持续推进。
```

开发顺序保持：

```text
make run
  -> make right
  -> make fast
  -> security & privacy
```

这样可以避免单个任务过长，也能保证每个阶段结束时系统都处于可运行、可验证、可继续演进的状态。
