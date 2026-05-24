# 07 安全与隐私

本文定义 `voice-gateway` 的安全边界、权限策略和隐私保护原则。

上级索引：[Voice Gateway 总设计](./DESIGN.md)  
相关文档：[01 架构与模块边界](./01-architecture.md)、[05 声纹识别](./05-speaker-identity.md)

## 1. 阶段目标

`voice-gateway` 会接收家庭麦克风音频，保存声纹 profile，并拥有控制音箱执行命令的能力。

因此本阶段目标是明确：

```text
谁可以连接 gateway？
谁可以控制音箱？
哪些数据可以保存？
哪些数据必须隔离或脱敏？
不同身份能执行哪些动作？
```

安全和隐私不应只依赖“服务跑在局域网里”这个假设。

## 2. 安全边界

默认原则：

- gateway 默认只绑定可信局域网地址。
- 接受音箱连接前应有认证机制。
- 不把 speaker client 的远程 shell 能力暴露给不可信客户端。
- 只有 gateway 内部可信模块可以调用 `run_shell`。
- 高权限命令需要明确策略和审计日志。

建议边界：

```text
untrusted client
  -/-> run_shell
  -/-> device control
  -/-> raw audio files
  -/-> speaker profiles

voice-gateway speaker client
  -> authenticated websocket
  -> limited device RPC

gateway internal modules
  -> controlled device commands
  -> explicit audit events
```

## 3. 设备认证

音箱连接 gateway 时，应能确认来源设备。

可选策略：

- 固定设备 token。
- 局域网内设备白名单。
- 设备 ID 与 token 绑定。
- 后续再增强为双向认证。

认证失败时：

- 拒绝连接。
- 不启动录音。
- 不下发播放或 shell 命令。
- 记录安全事件。

## 4. 远程命令权限

`run_shell` 是高风险能力。

原则：

- 不暴露为通用外部 API。
- 不接受来自用户自然语言的任意 shell 文本。
- 只允许 gateway 内部白名单动作调用。
- 每次调用都记录动作名、设备、原因和结果。

高风险命令应优先封装成明确的 `DeviceCommand`，而不是传递任意字符串。

## 5. 隐私策略

默认策略：

- 不长期保存原始音频。
- 调试保存音频必须显式开启。
- 调试音频应有保留周期。
- 声纹 profile 与普通日志分开保存。
- 日志中用户原话可以短期保留，但长期保留应摘要化或脱敏。
- 不把声纹 embedding 发给 Hermes 或第三方 LLM。

建议把数据分级：

```text
低敏：
  状态转移、耗时指标、错误码

中敏：
  ASR 文本、Hermes 请求摘要、TTS 文本

高敏：
  原始音频、声纹 embedding、speaker profile
```

## 6. 声纹权限

声纹状态：

```text
identified
unknown
ambiguous
```

权限建议：

- `identified`：允许个性化记忆和低风险家庭动作。
- `unknown`：允许普通问答，不允许写个人记忆。
- `ambiguous`：需要确认后才能执行敏感动作。

声纹识别失败不应阻塞普通问答，但必须限制高权限能力。

## 7. Hermes 数据边界

发送给 Hermes 的上下文应控制在必要范围内。

允许发送：

- 用户问题文本。
- 必要的短上下文。
- 说话人显示名或稳定 speaker id。
- 声纹识别状态和置信度摘要。

不应发送：

- 原始音频。
- 声纹 embedding。
- speaker profile 原始内容。
- 不相关的长期日志。
- 未脱敏的敏感家庭信息。

## 8. 审计事件

安全相关动作应记录审计事件：

```text
security.auth_failed
security.device_rejected
security.command_denied
security.command_executed
privacy.audio_saved
privacy.profile_updated
privacy.memory_write_denied
```

审计事件应避免记录敏感原文，只保留定位问题所需的元信息。

## 9. 验收标准

本阶段完成标准：

- 未认证连接不能控制音箱。
- 外部客户端不能直接调用 `run_shell`。
- 默认配置不会长期落盘原始音频。
- 调试音频保存有显式开关。
- 声纹数据有独立存储位置和访问边界。
- `unknown` 和 `ambiguous` 身份不能执行敏感动作。
- 敏感动作会记录审计事件。
- 发给 Hermes 的内容不包含声纹 embedding。
