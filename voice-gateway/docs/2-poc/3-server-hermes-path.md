# 3 路径：Mac Mini 服务侧接入 Hermes

## 定位

`2-*` 系列重点解决音箱侧：LX06 固件、SSH、音箱端 client、音箱到 Mac Mini server 的连接验证。

`3-*` 系列重点解决服务侧：把 Mac Mini 上已经跑通的 xiaozhi 示例 server，改造成接入 Hermes 的家庭语音桥接 server。这个系列已经完成，并作为 legacy route / manual rollback 保留。

当前状态：

```text
音箱侧 2-*：已跑通
  LX06 patched firmware
  -> SSH
  -> /data/open-xiaoai/client
  -> ws://192.168.1.9:4399
  -> Mac Mini xiaozhi 示例 server

服务侧 3-*：legacy route
  xiaozhi 示例 server
  -> Hermes server
  -> 小米云 ASR 文本 / Hermes / 文本播报
  -> 音箱播报回答
```

## 服务侧目标链路

```text
用户对小爱音箱 Pro LX06 说话
  -> open-xiaoai client
  -> WebSocket: ws://192.168.1.9:4399
  -> Mac Mini Hermes server
  -> 复用小爱识别文本
  -> Hermes 生成回答
  -> TTS 或音箱自身播报
  -> 小爱音箱 Pro 扬声器播放
```

## 当前前置条件

已经完成：

- LX06 刷写 `open-xiaoai` 补丁固件正常。
- SSH 可以连接音箱：`root@192.168.1.2`。
- 音箱端 client 已安装。
- 音箱端 client 可以稳定连接 Mac Mini 上的 server。
- xiaozhi Docker 示例 server 已验证最小闭环。

因此本阶段不再是验证硬件链路，而是验证 Mac Mini server 能否接入 Hermes。该阶段已经完成，后续 4-* 已改为本地 KWS/VAD + sherpa-onnx VAD/STT + TTS URL 主链路。

## 3-* 文档拆分

### 3.1 确认 Hermes 调用入口

文档：`3.1-hermes-entry.md`

目标：确认 Mac Mini 上的 Hermes 可以被 server 非交互调用。第一版优先使用 Hermes CLI，后续再考虑 API 或常驻进程。

### 3.2 梳理 xiaozhi 示例 server 协议

文档：`3.2-xiaozhi-protocol.md`

目标：明确 xiaozhi 示例中 WebSocket server、事件、音频流、识别文本、播报能力的代码入口。

### 3.3 实现最小 Hermes 闭环

文档：`3.3-minimal-hermes-loop.md`

目标：先用固定触发词跑通：用户文本 -> Hermes -> 短回答 -> 音箱播报。

### 3.4 STT / TTS / 运行方式

文档：`3.4-stt-tts-runtime.md`

目标：在最小 Hermes 闭环成功后，整理 3-* 阶段的运行方式，并记录后续进入 4-* 本地 STT/TTS 的边界。

## 历史推进原则

1. 先复用小爱原生识别文本，不急着做本地 STT。
2. 先复用音箱自身播报，不急着换 TTS 音色。
3. 先用固定触发词，不急着做连续对话。
4. 先前台运行和看日志，不急着做开机自启动。
5. 每次只验证一个环节，避免同时引入 STT、TTS、Hermes、打断等多个变量。

## 当时暂不做

最小 Hermes 闭环跑通前，暂不做：

- 多房间音箱路由。
- 长上下文记忆。
- 多用户声纹识别。
- 公网访问。
- 复杂权限体系。
- 完整连续对话和自然打断。
