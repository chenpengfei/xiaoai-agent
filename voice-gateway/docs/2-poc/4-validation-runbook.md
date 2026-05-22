# 4-* 本地语音接管逐步验证 Runbook

本文是 4-* 验证过程中的历史 runbook。当前 4-* 本地主链路已经验证通过；本文保留为排障和复现实验步骤参考。

## 当前现场检查结果

已确认当前 Mac Mini 上：

```text
port 4399: 已有 xiaozhi server 在监听
speaker connection: 192.168.1.2 -> 192.168.1.9:4399 ESTABLISHED
uv: /Users/chenpengfei/.local/bin/uv
ffmpeg: /opt/homebrew/bin/ffmpeg
ffprobe: /opt/homebrew/bin/ffprobe
```

这些检查曾用于进入 4-* 验证。当前主链路已经进入本地 KWS/VAD + Mac Mini sherpa-onnx VAD/STT + TTS URL 播放；3-* 只作为 legacy route / manual rollback 保留。

## 验证总原则

1. 一次只验证一个变量。
2. 每一步都要有可观察证据：日志、连接状态、音频文件、STT JSON 或播放结果。
3. 3-* legacy route 必须保留为 manual rollback：`你好 <问题>` 通过小米云 ASR 文本触发 Hermes。
4. 4-* 不要一开始就接 Hermes；先验证音频采集。
5. 如果某一步失败，停在该步排查，不要继续叠加后续环节。

---

# 阶段 0：确认 3-* legacy route 可用

## 0.1 确认 server 和音箱连接

在 Mac Mini 新终端运行：

```sh
cd /Users/chenpengfei/projects/vibe-coding/xiaoai-agent
netstat -anv -p tcp | grep '.4399 '
```

期望看到：

```text
192.168.1.9.4399  192.168.1.2.xxxxx  ESTABLISHED
*.4399            *.*                  LISTEN
```

如果没有 ESTABLISHED：

1. 确认音箱端 client 在运行。
2. 确认 `/data/open-xiaoai/server.txt` 指向 `ws://192.168.1.9:4399`。
3. 如果刚重启过音箱，建议重启 Mac 侧 xiaozhi server，避免 stale connection。

## 0.2 确认当前触发词是 Hermes

对音箱说：

```text
你好你是谁
```

期望 server 日志出现：

```text
🔥 收到指令: 你好你是谁
🤖 触发 Hermes: raw='你好你是谁', question='你是谁'
🤖 Hermes OpenAI-compatible API: base_url=http://127.0.0.1:8642/v1, model=hermes-agent
```

期望音箱播报 Hermes 回答。

如果这一步不成功，不影响 4-* 主链路判断，但会影响 manual rollback 能力。

---

# 阶段 1：确认现有音频流是否已经进入 Mac Mini

目标：不改 STT、不改 TTS，只确认 `record` stream 是否进入 server。

当前代码入口：

```text
open-xiaoai/examples/xiaozhi/src/server.rs:on_stream()
  tag == "record"
  -> PythonManager::call_fn("on_input_data", bytes)

open-xiaoai/examples/xiaozhi/xiaozhi/xiaoai.py:on_input_data()
  -> GlobalStream.input(...)
```

## 1.1 看现有日志是否有录音启动

打开 server 运行终端，重启一次 server：

```sh
cd /Users/chenpengfei/projects/vibe-coding/xiaoai-agent/open-xiaoai
./scripts/run-xiaozhi-hermes-dev.sh
```

启动后期望音箱播报：

```text
已连接
```

这个播报来自 `server.rs:test()`，它同时会调用：

```text
start_recording
start_play
```

## 1.2 观察说话时是否有识别事件

对音箱说：

```text
你好测试录音
```

先只观察：

```text
🔥 收到指令: ...
```

如果有，说明小米云 ASR event 正常；但这还不能证明本地音频流正常。

---

# 阶段 2：加最小音频探针

目标：给 `on_input_data()` 加一个低风险探针，只统计收到的音频 bytes，不保存大文件，不接 STT。

## 2.1 修改探针位置

文件：

```text
open-xiaoai/examples/xiaozhi/xiaozhi/xiaoai.py
```

函数：

```python
@classmethod
def on_input_data(cls, data: bytes):
```

临时增加日志，建议每累计约 1 秒音频打印一次，不要每帧打印。

期望日志格式：

```text
🎙️ record stream bytes_total=32000 chunk=2880 first16=...
```

## 2.2 重启 server

```sh
cd /Users/chenpengfei/projects/vibe-coding/xiaoai-agent/open-xiaoai
./scripts/run-xiaozhi-hermes-dev.sh
```

## 2.3 观察是否持续收到音频

说几句话：

```text
你好你是谁
你好今天天气怎么样
```

判断：

- 如果不说话也持续刷 bytes，说明当前录音是持续采集，需要后面用 VAD 切段。
- 如果只有说话/唤醒后才有 bytes，说明音箱端已经有一定 gating。
- 如果完全没有 bytes，先排查 `start_recording` 或音箱端 arecord。

本阶段只要求确认：Mac Mini 是否收到真实音频 bytes。

---

# 阶段 3：保存 5 个原始音频样本

目标：把收到的音频保存成文件，为 4.3 STT 做样本。

## 3.1 建目录

```sh
mkdir -p /Users/chenpengfei/projects/vibe-coding/xiaoai-agent/audio-samples/raw
mkdir -p /Users/chenpengfei/projects/vibe-coding/xiaoai-agent/audio-samples/utterances
```

## 3.2 保存策略

第一版不要做复杂 VAD。先手动窗口保存：

```text
收到第一帧后开始缓存
缓存 5 秒
写入 raw pcm 文件
停止缓存
```

保存路径：

```text
audio-samples/raw/YYYYMMDD-HHMMSS-sample.raw
```

当前 server 请求音箱录音参数是：

```text
sample_rate: 16000
channels: 1
bits_per_sample: 16
```

所以优先按这个格式解释：

```text
s16le / 16k / mono
```

## 3.3 录 5 条测试句

每次录一条：

```text
你好你是谁
你好今天天气怎么样
你好帮我讲一个十秒钟的小故事
你好大智若愚是什么意思
你好
```

每录完一条都记录文件名和句子。

---

# 阶段 4：把 raw 转成 wav 并试听

假设 raw 是 16k mono s16le，转换命令：

```sh
cd /Users/chenpengfei/projects/vibe-coding/xiaoai-agent
ffmpeg -f s16le -ar 16000 -ac 1 -i audio-samples/raw/<file>.raw audio-samples/utterances/<file>.wav
ffprobe audio-samples/utterances/<file>.wav
```

如果声音速度/音调正常，说明格式正确。

如果声音异常，尝试：

```sh
ffmpeg -f s16le -ar 24000 -ac 1 -i input.raw output-24k.wav
ffmpeg -f s16le -ar 48000 -ac 1 -i input.raw output-48k.wav
```

4.2 的完成标准是：至少 5 个可听的 wav 样本。

---

# 阶段 5：离线 STT 验证

进入 `4.3-mac-mini-stt.md`。

先不要实时接 server。对阶段 4 的 wav 样本做离线 STT。

每个样本输出：

```json
{
  "audio_path": "...wav",
  "text": "你好你是谁",
  "duration_ms": 1234,
  "engine": "..."
}
```

验收标准：核心语义正确即可，不要求标点完全一致。

---

# 阶段 6：TTS 单独验证

进入 `4.4-mac-mini-tts-playback.md`。

先用固定文本，不接 Hermes：

```text
你好，我是 Hermes，现在正在通过本地语音链路接管这个音箱。
```

验证顺序：

1. Mac Mini 生成音频。
2. Mac Mini 本机试听。
3. 音箱播放 Mac Mini 生成的音频。

只有音箱能播放 Mac Mini 生成的音频后，才进入端到端。

---

# 阶段 7：端到端 4-* 验证

进入 `4.5-end-to-end-local-hermes.md`。

完整链路：

```text
音频样本或实时 utterance
  -> STT
  -> 去掉“你好”前缀
  -> Hermes
  -> TTS
  -> 音箱播放
```

端到端日志必须包含：

```text
session_id
stt_ms
hermes_ms
tts_ms
playback_start_ms
total_ms
```

连续跑 5 次不失败，才认为 4-* 初步成功。

---

# 当前如何使用本文

如果要复现或排障，可以从阶段 0 开始：

1. 先确认 `你好你是谁` 仍能走 3-* legacy route。
2. 再确认 4-* 主链路里的音箱本地 KWS/VAD、record stream、Mac Mini sherpa-onnx VAD/STT、Hermes、TTS URL 和音箱播放。

日常开发应以 4-* 本地主链路为准，优先修本地链路问题，不把小米云 ASR 做成 runtime fallback。
