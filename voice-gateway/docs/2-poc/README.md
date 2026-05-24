# Voice Gateway PoC

这个仓库用于探索一条家庭语音助手链路：保留小爱音箱 Pro 作为家里的语音入口和语音出口，同时把复杂问题转交给 Mac Mini 上已经部署好的 Hermes 来回答。

上级索引：[Voice Gateway 开发路线](../roadmap.md)

## 文档结构

- [knowledge/README.md](./knowledge/README.md)：PoC 阶段沉淀出来的知识成果。
- [1-intent.md](./1-intent.md)：只描述意图、目标体验、非目标和待解决问题。
- [2-open-xiaoai-path.md](./2-open-xiaoai-path.md)：2 系列总览，重点解决音箱侧 open-xiaoai 链路。
- [2.1-lx06-firmware-ssh.md](./2.1-lx06-firmware-ssh.md)：步骤 2.1，LX06 固件、分区和 SSH。
- [2.2-speaker-client.md](./2.2-speaker-client.md)：步骤 2.2，音箱端 open-xiaoai client。
- [2.3-mac-mini-server.md](./2.3-mac-mini-server.md)：步骤 2.3，用示例 server 验证音箱侧 client 能连通 Mac Mini。
- [2.4-end-to-end-validation.md](./2.4-end-to-end-validation.md)：步骤 2.4，端到端验证和运维检查。
- [3-server-hermes-path.md](./3-server-hermes-path.md)：3 系列总览，重点解决 Mac Mini 服务侧接入 Hermes。
- [3.1-hermes-entry.md](./3.1-hermes-entry.md)：步骤 3.1，确认 Hermes 调用入口。
- [3.2-xiaozhi-protocol.md](./3.2-xiaozhi-protocol.md)：步骤 3.2，梳理 xiaozhi 示例 server 协议。
- [3.3-minimal-hermes-loop.md](./3.3-minimal-hermes-loop.md)：步骤 3.3，实现最小 Hermes 闭环。
- [3.4-stt-tts-runtime.md](./3.4-stt-tts-runtime.md)：步骤 3.4，STT / TTS / 运行方式。
- [3.4.1-runtime-dev-script.md](./3.4.1-runtime-dev-script.md)：步骤 3.4.1，开发期运行脚本和日志。
- [3.4.2-trigger-answer-experience.md](./3.4.2-trigger-answer-experience.md)：步骤 3.4.2，触发词和回答体验优化。
- [3.4.3-openai-compatible-api.md](./3.4.3-openai-compatible-api.md)：步骤 3.4.3，从 Hermes CLI 改为 Hermes Gateway OpenAI-compatible API。
- [4-local-voice-control-path.md](./4-local-voice-control-path.md)：4 系列总览，验证本地 KWS/VAD + Mac Mini STT/TTS，Hermes 完全接管音箱。
- [4-validation-runbook.md](./4-validation-runbook.md)：步骤 4 验证 runbook，汇总本地语音链路的手工验收方式。
- [4.1-local-voice-architecture.md](./4.1-local-voice-architecture.md)：步骤 4.1，本地语音链路架构和验收标准。
- [4.2-speaker-kws-vad-audio.md](./4.2-speaker-kws-vad-audio.md)：步骤 4.2，音箱侧 KWS/VAD 和音频采集验证。
- [4.3-mac-mini-stt.md](./4.3-mac-mini-stt.md)：步骤 4.3，Mac Mini STT 验证。
- [4.4-mac-mini-tts-playback.md](./4.4-mac-mini-tts-playback.md)：步骤 4.4，Mac Mini TTS 和音频回传验证。
- [4.5-end-to-end-local-hermes.md](./4.5-end-to-end-local-hermes.md)：步骤 4.5，端到端 Hermes 完全接管验证。
- [5-tts-quality-comparison.md](./5-tts-quality-comparison.md)：步骤 5，Edge TTS 与小米原生 TTS 的长文本播放效果对比。
- [n-current-status.md](./n-current-status.md)：动态进度，记录哪些步骤已完成、下一步做什么。

## 当前状态摘要

4-* 本地语音接管主链路已经跑通：

```text
小爱音箱 Pro LX06
  -> 音箱本地 KWS / VAD
  -> open-xiaoai record stream
  -> Mac Mini xiaozhi server
  -> sherpa-onnx Silero VAD
  -> sherpa-onnx Paraformer STT
  -> Hermes Gateway API Server
  -> Mac Mini TTS URL
  -> 音箱播放 Hermes 回答
```

`3-*` 小米云 ASR + Hermes 打断接管路线已经跑通，但现在只作为 legacy route / manual rollback 保留，不作为 4-* 的 runtime fallback。

详见 `n-current-status.md`。
