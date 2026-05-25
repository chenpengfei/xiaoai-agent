# 07 运维 Runbook

本文记录最小闭环运行期常见故障的检查和恢复步骤。

上级索引：[4 Ops 运维设计](./README.md)  
相关文档：[02 日志系统](./02-logging.md)、[03 指标系统](./03-metrics.md)、[04 链路追踪](./04-tracing.md)、[05 告警系统](./05-alerting.md)、[06 Grafana / Loki / Tempo / Alloy 可观测性栈](./06-grafana-loki-tempo-alloy.md)

## 0. 基础信息

主要日志：

```text
voice-gateway/logs/voice-gateway-minimal.log
```

主要运行入口：

```sh
cd /Users/chenpengfei/projects/vibe-coding/xiaoai-agent/voice-gateway
./scripts/run-voice-gateway-minimal.sh
```

依赖服务：

```text
Hermes Gateway: http://127.0.0.1:8642/v1
TTS HTTP:       http://192.168.1.9:8765
XiaoAI WS:      tcp/4399
```

## 1. voice-gateway 进程不可用

症状：

- 说唤醒词无反应。
- `voice_gateway_up == 0`。
- 日志不再更新。

检查：

```sh
pgrep -af 'server.xiaoai_runtime'
lsof -nP -iTCP:4399 -sTCP:LISTEN
tail -100 voice-gateway/logs/voice-gateway-minimal.log
```

恢复：

1. 如果 4399 被旧进程占用，先确认旧进程是否仍在工作。
2. 停掉失效进程后重新运行 `./scripts/run-voice-gateway-minimal.sh`。
3. 观察日志是否出现 `voice-gateway XiaoAI minimal runtime listening`。
4. 说“你好”，确认有 `wake_word.detected` 或相关 ASR 日志。

## 2. 音频流中断

症状：

- 进程仍在，但说话没有任何 ASR/VAD 事件。
- `record stream bytes_total` 长时间不增长。
- `voice_gateway_audio_last_seen_age_seconds` 持续升高。

检查：

```sh
grep 'record stream bytes_total=' voice-gateway/logs/voice-gateway-minimal.log | tail -20
grep -E 'device|connected|record|audio.chunk' voice-gateway/logs/voice-gateway-minimal.log | tail -80
lsof -nP -iTCP:4399
```

恢复：

1. 确认音箱与 Mac Mini 在同一网络。
2. 确认 open-xiaoai client 已连接到 Mac Mini 的 4399。
3. 重启 voice-gateway，让音箱重新连接。
4. 如果仍无音频，回到音箱侧检查 `arecord` 和 open-xiaoai client 日志。

## 3. Hermes 持续失败

症状：

- ASR 能识别问题，但没有回答。
- 日志出现 `hermes.failed` 或 `error.recovered`。
- Hermes 延迟接近 `VOICE_GATEWAY_OPENAI_TIMEOUT`。

检查：

```sh
grep -E 'hermes.started|hermes.completed|hermes.failed|error.recovered' voice-gateway/logs/voice-gateway-minimal.log | tail -80
curl -sS http://127.0.0.1:8642/v1/models
```

恢复：

1. 确认 Hermes Gateway 正在运行。
2. 确认 `VOICE_GATEWAY_OPENAI_BASE_URL` 和 `VOICE_GATEWAY_OPENAI_MODEL` 正确。
3. 确认 `VOICE_GATEWAY_OPENAI_API_KEY` 已从 `HERMES_ENV_PATH` 加载。
4. Hermes 恢复后，说一个短问题，观察 `hermes.completed`。

## 4. TTS 持续失败

症状：

- Hermes 有回答文本，但音箱不播放。
- 日志出现 `tts.failed` 或 edge-tts 错误。

检查：

```sh
grep -E 'tts.started|tts.completed|tts.failed|edge-tts|error.recovered' voice-gateway/logs/voice-gateway-minimal.log | tail -100
ls -lah voice-gateway/audio-samples/tts | tail
```

恢复：

1. 确认 `edge-tts` 可通过当前 Python 环境运行。
2. 确认 `VOICE_GATEWAY_TTS_OUTPUT_DIR` 可写。
3. 确认磁盘空间充足。
4. 重新运行最小闭环脚本并观察 `tts.completed`。

## 5. 音箱播放失败

症状：

- `tts.completed` 已出现。
- `playback.failed` 出现，或音箱无声。
- TTS 文件存在，但音箱拉不到 URL。

检查：

```sh
grep -E 'playback.started|playback.finished|playback.failed' voice-gateway/logs/voice-gateway-minimal.log | tail -80
curl -I http://192.168.1.9:8765/
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

恢复：

1. 确认 TTS HTTP 服务正在运行。
2. 确认 `VOICE_GATEWAY_TTS_HTTP_BASE_URL` 是音箱可访问的 Mac Mini 地址。
3. 用浏览器或 curl 从同网段访问最新 TTS 文件。
4. 确认音箱侧 `miplayer` 或 `start_play` 命令没有报错。

## 6. ASR 空文本或误识别

症状：

- VAD 有 `speech_ended`，但 ASR 文本为空或明显错误。
- 用户要重复说很多次。

检查：

```sh
grep -E 'wake_word.asr_completed|asr.completed|asr.empty|vad.speech' voice-gateway/logs/voice-gateway-minimal.log | tail -120
grep 'record stream bytes_total=' voice-gateway/logs/voice-gateway-minimal.log | tail -20
```

恢复：

1. 检查音频 RMS/peak 是否过低。
2. 调整 `VOICE_GATEWAY_VAD_GAIN_DB`。
3. 调整 `VOICE_GATEWAY_SILERO_VAD_THRESHOLD` 和 `VOICE_GATEWAY_SILERO_MIN_SILENCE`。
4. 确认模型路径 `VOICE_GATEWAY_SHERPA_MODEL_DIR` 正确。
5. 保留一个短音频样本用于离线复现。

## 7. 日志或磁盘异常

症状：

- 日志停写。
- TTS 文件无法生成。
- 系统磁盘空间不足。

检查：

```sh
df -h
du -sh voice-gateway/logs voice-gateway/audio-samples/tts
ls -lah voice-gateway/logs
```

恢复：

1. 清理过期 TTS 文件和旧日志。
2. 启用日志轮转。
3. 如果事件日志不可写，先恢复 stderr 输出，避免主链路被日志系统阻塞。

## 8. 运维栈自身异常

症状：

- Grafana Explore 查不到最近日志。
- Trace View 查不到 `voice.turn`。
- Discord 没收到测试告警。
- voice-gateway 仍在运行，但 dashboard 数据停止更新。

检查：

```sh
curl -sS http://127.0.0.1:3300/api/health
curl -sS http://127.0.0.1:3100/ready
curl -sS http://127.0.0.1:3200/ready
curl -sS http://127.0.0.1:12345/-/ready
tail -100 voice-gateway/logs/voice-gateway-minimal.log
```

恢复：

1. 先确认 `voice-gateway` 是否仍在产生日志。
2. 如果日志文件在增长但 Grafana 查不到，检查 Alloy file tail 和 Loki。
3. 如果日志可查但 trace 不出现，检查 OTLP endpoint、Alloy receiver 和 Tempo。
4. 如果 Grafana 告警触发但 Discord 无消息，检查 Discord contact point 和 webhook secret。
5. 恢复后发送一条测试告警，确认 Discord 收到 firing 和 resolved 通知。

## 9. 事故记录模板

```text
time:
severity:
symptom:
device_id:
conversation_id / turn_id:
trace_id:
first_bad_event:
last_good_event:
root_cause:
recovery_action:
follow_up:
```

每次 P1/P2 故障恢复后，至少记录根因、恢复动作和需要补的监控或告警规则。
