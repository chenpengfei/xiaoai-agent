# Voice Gateway

`voice-gateway` 是小爱音箱项目的长期 Mac Mini 侧语音网关工程。

它的目标是替代当前偏演示性质的 `open-xiaoai/examples/xiaozhi` 状态机，让小爱音箱退化为麦克风、扬声器和设备控制端，而由 Mac Mini 负责 VAD、ASR、声纹识别、连续对话、自然打断、Hermes 接入和 TTS/播放策略。

正式设计方案：

- [DESIGN.md](./DESIGN.md)

分阶段设计：

- [01 架构与模块边界](./docs/3-design/01-architecture.md)
- [02 最小闭环](./docs/3-design/02-minimal-loop.md)
- [03 连续对话](./docs/3-design/03-continuous-conversation.md)
- [04 自然打断](./docs/3-design/04-barge-in.md)
- [05 声纹识别](./docs/3-design/05-speaker-identity.md)
- [06 TTS 与播放控制](./docs/3-design/06-tts-playback.md)
- [07 可观测性](./docs/3-design/07-observability.md)
- [08 安全与隐私](./docs/3-design/08-security-privacy.md)

## 当前实现状态

已完成 `3-design/02-minimal-loop.md` 的最小工程闭环骨架：

```text
WakeupDetected
  -> PCM AudioChunk
  -> EnergyEndpointDetector
  -> final ASR
  -> HermesConnector
  -> TTSEngine
  -> PlaybackManager
  -> IDLE
```

核心代码在：

```text
voice_gateway/
  app.py                 # MinimalLoopGateway 状态机与离线 CLI
  adapters/              # OpenXiaoAI 协议归一化与音箱播放控制接口
  audio/endpointing.py   # 基础能量端点检测
  asr/                   # final ASR 接口、静态测试实现、sherpa-onnx adapter
  hermes/                # OpenAI-compatible Hermes connector
  playback/              # TTS file engine 与 PlaybackManager
  observability/         # 结构化事件
```

本地验证：

```sh
cd voice-gateway
python3 -m unittest discover -s tests
```

离线试跑一个 WAV，可先用静态 ASR、Echo Hermes 和内存播放资源验证状态机：

```sh
python3 -m voice_gateway.app \
  --wav /path/to/16k-mono-s16le.wav \
  --asr-text "你好你是谁" \
  --echo-hermes \
  --no-tts
```

真实音箱最小闭环运行入口：

```sh
cd /Users/chenpengfei/projects/vibe-coding/xiaoai-agent/voice-gateway

# 终端 1：Hermes Gateway
hermes gateway

# 终端 2：TTS 文件 HTTP 服务
./scripts/run-tts-http-server.sh

# 终端 3：voice-gateway 接管真实音箱最小闭环
./scripts/run-voice-gateway-minimal.sh
```

先说：

```text
你好
```

音箱收到 Mac Mini 侧唤醒后，会随机播放“我在 / 在 / 诶”中的一句作为唤醒反馈。随后再说：

```text
你是谁
```

预期链路：

```text
小爱音箱 record stream
  -> voice_gateway.xiaoai_runtime
  -> SherpaOnnxEndpointDetector + SherpaOnnxOfflineASREngine 检测“你好”
  -> XiaoAIDeviceController(tts_play.sh 随机播放“我在 / 在 / 诶”)
  -> 下一句用户语音
  -> SherpaOnnxEndpointDetector + SherpaOnnxOfflineASREngine
  -> OpenAICompatibleHermesConnector
  -> EdgeTTSFileEngine
  -> XiaoAIDeviceController(miplayer -f URL)
```
