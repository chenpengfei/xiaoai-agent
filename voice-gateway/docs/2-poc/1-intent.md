# 意图：Mac Mini 上的 Hermes 完全接管小爱音箱 Pro 的耳朵和嘴巴

## 背景

家里已经部署了：

- 小爱音箱 Pro，型号 `LX06`
- Mac Mini
- Mac Mini 上运行的 Hermes
- 小爱音箱 Pro 和 Mac Mini 位于同一个局域网

小爱音箱仍然是家里最自然的语音入口和语音出口，但它对复杂问题的回答质量不理想。Hermes 已经部署在 Mac Mini 上，更适合作为复杂问题的回答引擎。

之前探索过官方 Skill / Miclaw 路径，但公开资料没有证明 Miclaw Agent 可以接收小爱音箱 Pro 的麦克风输入。因此当前目标转向 `open-xiaoai` 思路：在 `LX06` 音箱端运行 client，把音箱的麦克风和扬声器桥接到 Mac Mini，由 Mac Mini 上的 Hermes 完成复杂问题处理。

当前已经验证：LX06 刷写 `open-xiaoai` 补丁固件正常，SSH 可以连接音箱，音箱端 client 可以稳定连接 Mac Mini 上的 server；4-* 本地语音接管主链路已经跑通，Hermes 可以通过本地 STT 和 TTS URL 接管音箱问答。

## 目标

希望建立一条“完全接管”式家庭语音桥接链路：

1. 语音输入仍然走小爱音箱 Pro 的麦克风。
2. 音频流通过 `open-xiaoai` client 转发到 Mac Mini。
3. 音箱侧负责基础 KWS / VAD 和采集触发。
4. Mac Mini 负责服务端 VAD / STT。
5. Mac Mini 调用 Hermes 生成回答。
6. Mac Mini 负责 TTS 或音频生成。
7. 语音输出仍然从小爱音箱 Pro 扬声器播放。
8. 尽量获得比轮询小爱云端对话记录更低的延迟、更自然的打断和连续对话体验。

## 理想体验

用户主要仍然对小爱音箱说话。

可能的触发方式：

- 使用 `open-xiaoai` 示例里的自定义唤醒词。
- 使用小爱原生唤醒后，由音箱端事件或 Server 逻辑判断是否转给 Hermes。
- 先做固定触发词，后续再优化连续对话和打断。

期望链路：

```text
用户语音
  -> 小爱音箱 Pro LX06
  -> open-xiaoai client
  -> WebSocket 连接到 Mac Mini 上的 server
  -> Mac Mini server 收到麦克风音频流
  -> STT 转文字
  -> Hermes 生成回答
  -> TTS 生成语音
  -> Mac Mini server 下发音频流或播放指令
  -> 小爱音箱 Pro 播报
```

## 当前路线

当前采用 `open-xiaoai` 思路，而不是官方 Skill / Miclaw 路径。

选择原因：

- `LX06` 是 `open-xiaoai` 明确支持的型号。
- `open-xiaoai` 更接近直接使用音箱的“耳朵”和“嘴巴”。
- 它可以转发麦克风音频流、语音识别结果、播放状态等事件到 Mac Mini。
- 它可以响应 Server 指令，让音箱播放音频流或执行脚本。
- 相比 `mi-gpt` / `xiaogpt` 轮询云端对话记录，理论上延迟更低，也更适合连续对话和打断。

主要代价：

- 需要刷补丁固件。
- 需要 SSH 进入音箱并安装 client。
- 有保修、变砖、稳定性和安全风险。
- `open-xiaoai` 原仓库已归档，不再维护。
- 示例程序默认更适合局域网测试，不适合直接暴露到公网。

## 当前阶段边界

本地 Hermes 接管闭环已经跑通：

```text
小爱音箱 Pro LX06
  -> 音箱本地 KWS / VAD
  -> open-xiaoai record stream
  -> Mac Mini sherpa-onnx VAD / STT
  -> Hermes
  -> Mac Mini TTS URL
  -> 音箱播放
```

下一阶段目标是把 PoC 中已验证的链路收口成稳定的 `voice-gateway` 工程设计，但仍然不一次性追求所有高级能力。

当前暂不做：

- 长上下文记忆
- 多房间音箱路由
- 复杂唤醒词体验
- 公网部署

## 已验证问题

- 当前 `LX06` 固件版本可以刷入 `open-xiaoai` 补丁固件。
- 刷机后 SSH 可以连接音箱。
- 音箱端 client 可以稳定连接 Mac Mini 上的 server。
- 当前 xiaozhi 示例 server 可以完成最小闭环。
- Server 可以让音箱播放测试内容。
- 音箱本地 KWS / VAD 可以作为本地语音入口。
- Mac Mini 可以用 sherpa-onnx 做 VAD / STT。
- Hermes 可以通过本地 STT question 触发。
- Mac Mini TTS URL 可以让音箱播放回答。

## 后续设计问题

- 如何把 PoC 中的 xiaozhi server 收口成长期运行的 `voice-gateway`。
- 如何实现稳定的连续对话、自然打断、声纹识别和错误恢复。
- 如何完善观测、隐私、安全和常驻运行。

## 文档分工

本文件只描述“为什么做”和“希望达到什么体验”。

实现路径、步骤拆解和当前进度分别放在：

- `2-open-xiaoai-path.md`：2 系列总览，重点解决音箱侧 open-xiaoai 链路。
- `2.1-*`、`2.2-*`、...：音箱侧具体步骤，例如固件、SSH、client 和端到端验证。
- `3-server-hermes-path.md`：3 系列总览，重点解决 Mac Mini 服务侧接入 Hermes。
- `3.1-*`、`3.2-*`、...：服务侧具体步骤，例如 Hermes 调用入口、server 协议、最小 Hermes 闭环、STT/TTS/运行方式。
- `n-current-status.md`：动态更新当前哪些步骤已经完成、下一步做什么。
