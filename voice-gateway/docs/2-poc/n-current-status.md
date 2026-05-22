# 当前进度：Open-XiaoAI 到 Hermes 接管

这个文件是 PoC 阶段的当前事实源，记录哪些步骤已经走完、当前主链路是什么、旧路线如何保留。

## 当前总体状态

`2-*` 音箱侧链路已经跑通。

`3-*` 小米云 ASR + Hermes 打断接管路线已经跑通，但现在只作为 legacy route / manual rollback 保留，不作为当前主链路，也不作为 4-* 的运行时自动 fallback。

`4-*` 本地语音接管路线已经验证通过，当前主链路是：

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

当前触发方式：

```text
本地语音识别文本中包含“你好”
  -> 取最后一个“你好”之后的内容作为 question
  -> source="local-stt" 触发 Hermes
```

当前主链路不走小米云 ASR fallback。

## 已完成：2 系列音箱侧

### 2.1 LX06 固件、分区和 SSH

文档：`2.1-lx06-firmware-ssh.md`

状态：完成。

已确认：

- LX06 固件版本是 `1.94.13`。
- 普通 open-xiaoai patched 固件已经验证正常。
- 音箱真实 IP 是 `192.168.1.2`。
- 音箱 MAC 是 `50:88:11:6f:f2:a8`。
- `192.168.1.10` 不是这台音箱。
- `system0 = LX06_1.94.13_patched.squashfs`。
- `system1 = LX06_1.94.13.squashfs`。
- SSH 可登录：`root@192.168.1.2`。

### 2.2 音箱端 open-xiaoai client

文档：`2.2-speaker-client.md`

状态：完成。

已确认：

- `/data/open-xiaoai/client` 已安装。
- `/data/open-xiaoai/server.txt` 已设置为 `ws://192.168.1.9:4399`。
- client 可以连接 Mac Mini server。
- `/data/init.sh` 已配置，用于开机自启动 client。

### 2.3 示例 server 连通性验证

文档：`2.3-mac-mini-server.md`

状态：完成。

已确认：

- Mac Mini 当前 IP 是 `192.168.1.9`。
- xiaozhi 示例 server 可监听 `4399`。
- 音箱 client 可连接该 server。
- server 下发测试指令时，音箱可以播报测试内容。

### 2.4 端到端验证

文档：`2.4-end-to-end-validation.md`

状态：完成。

已确认：

- 音箱 client 连接成功日志出现过：`✅ 已连接: "ws://192.168.1.9:4399"`。
- server 下发测试指令时，音箱播报过“已连接”。

## 已完成：3 系列 legacy route

总览：`3-server-hermes-path.md`

状态：完成，作为旧路线 / 手动回滚方案保留。

已跑通：

```text
你好 <问题>
  -> 小爱原生识别文本
  -> config.py:before_wakeup()
  -> Hermes Gateway API Server /v1/chat/completions
  -> 短回答
  -> speaker.abort_xiaoai()
  -> speaker.play(text=answer)
  -> 音箱播报
```

当前定位：

```text
legacy route: 3-* 小米云 ASR + Hermes 打断接管
manual rollback: 4-* 主链路异常时可手动回退到 3-*
runtime fallback: 当前不做自动切回小米云 ASR
```

## 已完成：4 系列本地语音接管

总览：`4-local-voice-control-path.md`

状态：已验证。

目标链路已经跑通：

```text
音箱本地 KWS / VAD
  -> 音箱 record stream
  -> Mac Mini sherpa-onnx VAD / STT
  -> Hermes Gateway API Server
  -> Mac Mini TTS URL
  -> 音箱扬声器播放
```

### 4.1 本地语音链路架构和验收标准

文档：`4.1-local-voice-architecture.md`

状态：完成。

已明确：

- 音箱侧负责 KWS / VAD、麦克风采集、record stream 和播放。
- Mac Mini 侧负责二次 VAD / STT、Hermes 调用、TTS 生成、播放 URL 和日志。
- 3-* 只作为 legacy route / manual rollback，不作为 runtime fallback。

### 4.2 音箱侧 KWS/VAD 和音频采集验证

文档：`4.2-speaker-kws-vad-audio.md`

状态：完成。

已确认音箱本地可以做 KWS / VAD，并通过 open-xiaoai record stream 把音频送到 Mac Mini。

### 4.3 Mac Mini STT 验证

文档：`4.3-mac-mini-stt.md`

状态：完成。

已确认：

- 使用 `sherpa-onnx` 作为 4-* 主线 STT。
- Mac Mini 侧使用 Silero VAD + Paraformer STT。
- 本地 STT 结果可按最后一次“你好”截取 question。

### 4.4 Mac Mini TTS 和音频回传验证

文档：`4.4-mac-mini-tts-playback.md`

状态：完成。

已确认：

- Mac Mini 可以生成 TTS 音频。
- TTS URL 已经可以被音箱播放。
- `speaker.play(text=...)` 仅作为 3-* legacy route 的文字播报能力保留。

### 4.5 端到端 Hermes 完全接管验证

文档：`4.5-end-to-end-local-hermes.md`

状态：完成。

已跑通：

```text
用户说“你好 <问题>”
  -> 音箱本地 KWS / VAD
  -> record stream
  -> Mac Mini sherpa-onnx VAD / STT
  -> Hermes
  -> Mac Mini TTS URL
  -> 音箱播放
```

## 近期建议顺序

1. 保持 4-* 本地主链路稳定，优先修本地音频、VAD/STT、TTS URL 和播放链路。
2. 保留 3-* 旧路线，但只作为手动回滚方案。
3. 进入 `3-design`，把 PoC 中已验证的能力收口成 `voice-gateway` 的稳定工程设计。
