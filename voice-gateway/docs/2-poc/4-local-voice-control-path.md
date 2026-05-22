# 4 路径：本地 KWS/VAD + Mac Mini STT/TTS，Hermes 完全接管音箱

## 定位

`3-*` 系列已经解决了“音箱接入 Hermes”的问题：

```text
用户说：你好 <问题>
  -> 小爱原生 ASR / 小米云
  -> open-xiaoai 捕获识别文本
  -> Mac Mini xiaozhi server
  -> Hermes Gateway API Server
  -> speaker.abort_xiaoai()
  -> speaker.play(text=Hermes answer)
  -> 音箱播报 Hermes 回答
```

这个阶段的特点是：音箱、米家/小米云和 Hermes 同时存在。小米云仍然会先听到用户问题，也可能准备回答；Hermes 需要通过 `speaker.abort_xiaoai()` 打断小米云播报，再让音箱播报 Hermes 的结果。

`4-*` 系列已经验证第二条、更快也更彻底的路线：

```text
音箱本地 KWS / VAD
  -> open-xiaoai client 捕获音频
  -> Mac Mini sherpa-onnx VAD / STT
  -> Hermes
  -> Mac Mini TTS URL
  -> 音箱播放
```

这条路线不再依赖小米云 ASR/TTS 作为主路径。3-* 小米云 ASR + Hermes 打断接管路线只作为 legacy route / manual rollback 保留，不作为运行时自动 fallback。

## 为什么需要 4-* 系列

当前 3-* 路线已经可用，但有天然限制：

1. 用户语音先经过小米云 ASR，Hermes 只能复用小米云识别文本。
2. 小米云可能同时生成回答，Hermes 需要打断它。
3. 打断、等待 TTS 恢复、再播报 Hermes，会带来额外延迟。
4. 触发词“你好”仍然是小爱原生指令链路的一部分。
5. 只要小米云链路变化，Hermes 体验就可能受影响。

4-* 路线已经验证：

1. 本地唤醒更快。
2. 本地 VAD 能更准确控制录音起止。
3. Mac Mini STT 能直接拿到用户语音，不依赖小米云文本。
4. Mac Mini TTS 能生成可播放音频，并通过 TTS URL 让音箱播放。
5. Hermes 可以变成音箱的主控制面，而不是小米云之后的补丁。

## 目标体验

目标用户体验：

```text
用户：你好，今天适合带孩子去哪玩？
音箱：<很快停止聆听>
Mac Mini：本地 STT 得到文本
Hermes：生成短回答
Mac Mini：TTS 生成音频
音箱：直接播放 Hermes 音频
```

理想延迟目标：

```text
唤醒检测：< 300 ms
VAD 结束判断：< 800 ms after speech end
STT：< 1.5 s
Hermes 首包/完整回答：取决于模型，目标 < 3 s
TTS 首包：< 1 s
总体首句播报：尽量 < 3-5 s
```

## 4-* 总体链路

```text
小爱音箱 Pro LX06
  -> open-xiaoai patched firmware
  -> 本地 client 捕获麦克风音频
  -> 音箱本地 KWS 检测“你好”或本地唤醒词
  -> 音箱本地 VAD 判断用户说话起止
  -> WebSocket: ws://192.168.1.9:4399
  -> Mac Mini voice server
  -> sherpa-onnx Silero VAD
  -> sherpa-onnx Paraformer STT
  -> Hermes Gateway API Server: http://127.0.0.1:8642/v1
  -> Mac Mini TTS URL
  -> 音箱扬声器播放
```

## 和 3-* 的关系

`3-*` 不删除，作为 legacy route / manual rollback。

```text
3-*：小米云 ASR/TTS + Hermes 打断接管
  优点：已经跑通；实现简单；复用小爱原生能力
  缺点：延迟更高；小米云仍然参与；需要打断

4-*：本地 KWS/VAD + Mac Mini STT/TTS + Hermes 主控
  优点：不走小米云 ASR fallback；可控；Hermes 完全接管
  缺点：后续仍需要工程化状态机、观测、常驻和恢复策略
```

推进原则：

1. 保留 3-* 可用状态，用于手动回滚。
2. 4-* 主链路失败时优先修本地音频、VAD/STT、TTS URL 和播放链路。
3. 不把 3-* 做成 4-* 的 runtime fallback，避免主链路问题被小米云链路掩盖。
4. 所有验证都要有日志、录音样本或明确可观察结果。

## 4-* 文档拆分

### 4.1 本地语音链路架构和验收标准

文档：`4.1-local-voice-architecture.md`

目标：明确完整架构、模块边界、数据格式、延迟指标和 legacy route / manual rollback 策略。

### 4.2 音箱侧 KWS/VAD 和音频采集验证

文档：`4.2-speaker-kws-vad-audio.md`

目标：确认音箱本地能稳定捕获唤醒、VAD 和录音片段，并把音频送到 Mac Mini。

### 4.3 Mac Mini STT 验证

文档：`4.3-mac-mini-stt.md`

目标：验证 Mac Mini 能用 sherpa-onnx 把音箱传来的音频转成文本，先离线样本，再接实时链路。

### 4.4 Mac Mini TTS 和音频回传验证

文档：`4.4-mac-mini-tts-playback.md`

目标：验证 Hermes 文本回答可以由 Mac Mini TTS 生成音频，并通过 TTS URL 让音箱扬声器播放。

### 4.5 端到端 Hermes 完全接管验证

文档：`4.5-end-to-end-local-hermes.md`

目标：串起音箱本地 KWS/VAD -> record stream -> Mac Mini sherpa-onnx VAD/STT -> Hermes -> TTS URL -> 音箱播放，并和 3-* legacy route 做延迟、稳定性和体验对比。

## 暂不做

4-* 验证阶段暂不做：

- 多音箱同步。
- 多用户声纹识别。
- 情绪识别。
- 长时间连续监听全部上传。
- 公网远程控制。
- 米家设备控制自动化。
- 完整家庭权限体系。

## 当前下一步

进入 `3-design`，把 4-* 已验证能力工程化为稳定、可维护、可观测的 `voice-gateway`。
