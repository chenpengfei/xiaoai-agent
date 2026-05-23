# Voice Gateway 总设计

`voice-gateway` 是运行在 Mac Mini 上的长期语音网关服务，用于接管刷写补丁后的小爱音箱语音链路。

这份文档只保留总览、阶段边界和文档索引。具体设计拆分到本目录下的阶段文档，按“先最小闭环，再逐步加能力”的顺序组织。

上级索引：[Voice Gateway 开发路线](../roadmap.md)

## 1. 总体目标

目标架构：

```text
小爱音箱
  -> 持续麦克风 PCM 音频流
  -> Mac Mini voice-gateway
  -> VAD / ASR / 声纹识别 / 对话状态机
  -> Hermes / 工具 / 记忆
  -> TTS 或音频播放
  -> 小爱音箱
```

长期方向：

- 音箱退化为麦克风、扬声器和设备控制端。
- Mac Mini 负责 VAD、ASR、声纹识别、连续对话、自然打断、Hermes 接入和 TTS/播放策略。
- 目标链路不依赖外部云 ASR。

## 2. 文档阅读顺序

建议按下面顺序阅读和实现：

1. [01 架构与模块边界](./01-architecture.md)
2. [02 最小闭环](./02-minimal-loop.md)
3. [03 连续对话](./03-continuous-conversation.md)
4. [04 自然打断](./04-barge-in.md)
5. [05 声纹识别](./05-speaker-identity.md)
6. [06 TTS 与播放控制](./06-tts-playback.md)
7. [07 安全与隐私](./07-security-privacy.md)
8. [运维设计：可观测性、日志、监控与告警](../4-ops/README.md)

## 3. 阶段关系

```text
01 架构与模块边界
  -> 定义模块、数据结构、状态机总形态

02 最小闭环
  -> 音箱 PCM -> VAD -> ASR -> Hermes -> 音箱播报

03 连续对话
  -> 在最小闭环之后加入 FOLLOWUP_WAIT 和多 turn 上下文

04 自然打断
  -> 在 SPEAKING 状态保留监听，检测插话并停止播放

05 声纹识别
  -> 给每轮用户语音附加 speaker identity，用于记忆和权限

06 TTS 与播放控制
  -> 设计 gateway 管理的 TTS、播放生命周期、取消、打断和播放参考音频

07 安全与隐私
  -> 定义网络边界、设备认证、权限策略和隐私保护原则

4 Ops 运维设计
  -> 定义结构化事件、日志、指标、trace、告警和故障诊断策略
```

## 4. 总体模块

```text
voice-gateway
  OpenXiaoAIAdapter
  AudioIngest
  VAD / Endpointing
  ASR Pipeline
  Speaker Identity
  Dialogue State Machine
  Hermes Connector
  TTS Engine
  Playback Manager
  Observability
```

模块边界详见 [01 架构与模块边界](./01-architecture.md)。

## 5. 目标目录结构

```text
xiaoai-agent/
  voice-gateway/
    README.md
    docs/
      README.md
      roadmap.md
      1-idea/
      2-poc/
        knowledge/
      3-design/
        DESIGN.md
        01-architecture.md
        02-minimal-loop.md
        03-continuous-conversation.md
        04-barge-in.md
        05-speaker-identity.md
        06-tts-playback.md
        07-security-privacy.md
      4-ops/
        README.md
        01-observability.md
        02-logging.md
        03-metrics.md
        04-tracing.md
        05-alerting.md
        06-grafana-loki-tempo-alloy.md
        07-ops-runbook.md
    pyproject.toml
    voice_gateway/
      __init__.py
      app.py
      config.py
      adapters/
      audio/
      asr/
      identity/
      dialogue/
      hermes/
      playback/
      observability/
    tests/
```

## 6. 设计原则

- 先建立清晰边界，再接复杂模型。
- 最小闭环只追求端到端可用，不混入连续对话、声纹和自然打断。
- 每个阶段只新增一个主要能力。
- VAD、ASR、声纹和 TTS 都通过接口隔离，不把业务状态机绑定到具体模型。
- 业务状态机只依赖 gateway 标准化后的内部事件，不绑定设备侧日志或私有事件格式。
- 音箱远程 shell 能力只允许由可信 gateway 内部调用。
