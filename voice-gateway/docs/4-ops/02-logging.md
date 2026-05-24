# 02 日志系统

本文定义 `xiaoai-agent` 的日志采集、格式、留存、查询和脱敏策略。

上级索引：[4 Ops 运维设计](./README.md)  
相关文档：[01 可观测性总设计](./01-observability.md)、[安全与隐私](../3-design/07-security-privacy.md)

## 1. 目标

日志系统的第一目标是复盘单次语音请求，第二目标是给监控和告警提供事实来源。

最小闭环阶段日志必须能回答：

- 音箱是否仍在向 Mac Mini 推送音频。
- 唤醒词是否被识别。
- 问题 ASR 是否为空或识别错误。
- Hermes、TTS、播放分别花了多久。
- 失败后是否回到可用状态。

## 2. 日志分层

```text
console log
  -> 给开发者实时观察

structured event log
  -> JSONL，一行一个事件，是运维查询和指标派生的主来源

probe log
  -> 低频音频流探针，用于判断 record stream 是否仍有数据

audit log
  -> 高权限动作、设备 shell、配置变更和人工恢复动作
```

当前 `voice-gateway/scripts/run-voice-gateway-minimal.sh` 会把 stdout/stderr 写入：

```text
voice-gateway/logs/voice-gateway-minimal.log
```

后续应将事件日志、自然语言运行日志和审计日志拆成三个文件：

```text
voice-gateway/logs/events.jsonl
voice-gateway/logs/runtime.log
voice-gateway/logs/audit.jsonl
```

日志查询系统采用 [Grafana / Loki / Tempo / Alloy 可观测性栈](./06-grafana-loki-tempo-alloy.md)：

```text
voice-gateway/logs/events.jsonl
voice-gateway/logs/runtime.log
voice-gateway/logs/audit.jsonl
  -> Grafana Alloy
  -> Grafana Loki
  -> Grafana Explore
```

## 3. JSONL 事件格式

每条结构化事件必须包含：

```json
{
  "event": "asr.completed",
  "timestamp_ms": 123456789,
  "level": "info",
  "service": "voice-gateway",
  "trace_id": "4bf92f...",
  "span_id": "00f067...",
  "device_id": "xiaoai-speaker",
  "conversation_id": "c_...",
  "turn_id": "t_..."
}
```

字段约定：

- `event`：稳定事件名，使用 `domain.action`。
- `timestamp_ms`：Unix epoch 毫秒，便于跨进程比较。
- `level`：`debug`、`info`、`warning`、`error`。
- `service`：产生事件的服务名。
- `trace_id` / `span_id`：OpenTelemetry trace 关联字段，用于从日志跳转到 Tempo trace。
- `device_id`：设备维度。
- `conversation_id` / `turn_id` / `playback_id`：链路维度。
- `latency_ms`：耗时统一使用毫秒整数。
- `total_ms`：一轮 turn 的端到端耗时。
- `stage_ms`：一轮 turn 各阶段耗时摘要，只在 `turn.completed` / `turn.failed` 中出现。
- `slowest_stage`：当前 turn 中耗时最长的阶段。
- `error_type` / `error`：错误类别和精简错误信息。

不要把长文本栈、完整用户原话、API key、文件大块内容写进默认事件。需要调试时写入短期 debug 日志，并设置留存周期。

## 4. 事件级别

运行时提供几项日志级别控制：

```bash
VOICE_GATEWAY_LOG_LEVEL=INFO
VOICE_GATEWAY_EVENT_LEVEL=INFO
VOICE_GATEWAY_AUDIO_PROBE_LEVEL=WARN
VOICE_GATEWAY_SUPPRESS_AUDIO_CHUNKS=1
VOICE_GATEWAY_PROBE_INTERVAL_BYTES=160000
```

- `VOICE_GATEWAY_LOG_LEVEL`：普通运行日志最低输出级别。默认 `INFO`，保留关键运行状态并过滤 `DEBUG`。
- `VOICE_GATEWAY_EVENT_LEVEL`：结构化事件日志 `events.jsonl` 最低记录级别。默认 `INFO`，保留主链路事件给 Loki/Grafana 使用。
- `VOICE_GATEWAY_AUDIO_PROBE_LEVEL`：音频探测日志的最低输出级别。探测日志按 `INFO` 级别处理，默认 `WARN` 会抑制 `record stream bytes_total=...`；排查音频/VAD 时可临时改为 `INFO` 或 `DEBUG`。
- `VOICE_GATEWAY_SUPPRESS_AUDIO_CHUNKS`：是否抑制 `audio.chunk.received` 结构化事件，`1` 表示抑制，适合长期运行。
- `VOICE_GATEWAY_PROBE_INTERVAL_BYTES`：音频探测采样间隔，单位字节。`160000` 对 16kHz、16-bit、单声道 PCM 约等于 5 秒。

```text
debug
  高频或临时诊断，例如 audio.chunk.received

info
  主链路生命周期，例如 asr.completed / playback.finished

warning
  可自动恢复但需要观察，例如 audio.stream.gap / asr.empty_question

error
  当前 turn 失败或 runtime worker 异常
```

`audio.chunk.received` 默认可抑制。当前环境变量 `VOICE_GATEWAY_SUPPRESS_AUDIO_CHUNKS=1` 适合长期运行，排查音频中断时再临时打开或依赖 probe log。

## 5. 日志查询

人工翻阅日志时优先使用 Grafana Explore。`jq` 和 `grep` 保留为本机兜底排障方式。

Grafana Explore 常用 LogQL：

按 turn 复盘：

```logql
{service="voice-gateway", log_type="events"} | json | turn_id="t_xxx"
```

看最近失败：

```logql
{service="voice-gateway", log_type="events"} | json | level="error"
```

或：

```logql
{service="voice-gateway", log_type="events"} | json | event=~".*\\.failed"
```

看 Hermes 慢请求：

```logql
{service="voice-gateway", log_type="events"} | json | event="hermes.completed" | latency_ms > 10000
```

看端到端慢请求：

```logql
{service="voice-gateway", log_type="events"} | json | event=~"turn\\.(completed|failed)" | total_ms > 15000
```

看最慢环节分布：

```logql
{service="voice-gateway", log_type="events"} | json | event=~"turn\\.(completed|failed)" | line_format "{{.slowest_stage}} {{.total_ms}}ms"
```

看音频流探针：

```logql
{service="voice-gateway", log_type="runtime"} |= "record stream bytes_total="
```

从 trace 反查日志：

```logql
{service="voice-gateway", log_type="events"} | json | trace_id="4bf92f..."
```

本机兜底查询：

按 turn 复盘：

```sh
jq -c 'select(.turn_id == "t_xxx")' voice-gateway/logs/events.jsonl
```

看最近失败：

```sh
jq -c 'select(.level == "error" or (.event | endswith(".failed")))' voice-gateway/logs/events.jsonl | tail -50
```

看 Hermes 延迟：

```sh
jq -r 'select(.event == "hermes.completed") | [.timestamp_ms, .turn_id, .latency_ms] | @tsv' voice-gateway/logs/events.jsonl
```

看音频流是否仍在推进：

```sh
grep 'record stream bytes_total=' voice-gateway/logs/voice-gateway-minimal.log | tail -20
```

## 6. 留存与轮转

本机第一阶段建议：

```text
runtime.log
  rotate: 50 MB
  keep: 7 days

events.jsonl
  rotate: 100 MB
  keep: 14 days

audit.jsonl
  rotate: 10 MB
  keep: 90 days

debug audio / raw transcript
  keep: 24 hours or manual opt-in
```

日志轮转必须避免截断正在写入的文件。优先使用 `newsyslog`、`logrotate` 或应用内部按日期新建文件。

## 7. 脱敏策略

默认不记录：

- API key、token、cookie。
- 声纹 profile 原始特征。
- 长时间原始音频。
- 完整设备 shell 输出。

默认允许短期记录：

- ASR 原文和归一化文本。
- Hermes 响应摘要。
- TTS 文本长度和播放资源元信息。

长期保留时应把用户原话转换为：

```text
text_length
text_hash
language
intent_label
```

## 8. 验收标准

- 能按 `turn_id` 查询一轮完整链路。
- Grafana Explore 可以查询 `events`、`runtime` 和 `audit` 三类日志。
- 能从日志看出 ASR、Hermes、TTS、播放耗时。
- 能从 `turn.completed` / `turn.failed` 直接看出 `slowest_stage`。
- 音频流探针可判断 record stream 是否持续到达。
- 失败事件有 `error_type` 或明确模块名。
- 日志不会长期保留未脱敏的完整用户原话。
- 日志轮转不会影响主进程运行。
