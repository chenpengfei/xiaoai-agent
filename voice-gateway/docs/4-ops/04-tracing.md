# 04 链路追踪

本文定义 `voice-gateway` 的 trace 方案，用于定位单次语音请求断在哪个环节。

上级索引：[4 Ops 运维设计](./README.md)  
相关文档：[01 可观测性总设计](./01-observability.md)、[02 日志系统](./02-logging.md)、[06 Grafana / Loki / Tempo / Alloy 可观测性栈](./06-grafana-loki-tempo-alloy.md)

## 1. 选型结论

采用：

```text
OpenTelemetry SDK
  -> 在 voice-gateway 内创建 trace/span

Grafana Alloy
  -> 接收 OTLP trace 并转发

Grafana Tempo
  -> 存储 trace

Grafana
  -> Trace View、trace to logs、trace to metrics
```

日志、指标、trace 的职责：

```text
metric
  -> 发现系统整体是否健康

trace
  -> 定位一次请求卡在哪个环节

log
  -> 解释具体错误和上下文
```

## 2. 目标链路

每一轮用户输入和助手回答都应形成一条端到端 trace：

```text
trace: voice.turn
  span: wake_word
  span: audio_ingest
  span: vad_endpointing
  span: asr
  span: hermes
  span: tts
  span: playback
```

如果某个环节失败，trace 应直接显示失败 span：

```text
voice.turn  12.4s  error
  wake_word          220ms  ok
  audio_ingest       300ms  ok
  vad_endpointing    820ms  ok
  asr               1100ms  ok
  hermes           10000ms  timeout
  tts                  -    skipped
  playback             -    skipped
```

## 3. Trace 边界

推荐边界：

```text
trace_id
  一轮 voice turn 的端到端链路。

root span: voice.turn
  从 wakeup/question accepted 开始，到 playback finished / turn failed 结束。

child span
  每个主要模块的一次调用或处理阶段。
```

连续对话中：

```text
conversation_id: c_1
  trace_id: turn 1
  trace_id: turn 2
  trace_id: turn 3
```

也就是说，一个 `conversation_id` 可以包含多个 trace；每个 `turn_id` 对应一条主 trace。

## 4. Span 设计

必须优先覆盖：

```text
voice.turn
audio_ingest
vad_endpointing
asr
hermes
tts
playback
```

后续能力再补：

```text
speaker_identity
barge_in_detect
memory_read
memory_write
tool_call
device_command
```

每个 span 至少包含：

```text
span.name
span.status
start_time
end_time
duration_ms
error_type when failed
error when failed
```

## 5. Attribute 约定

通用 attributes：

```text
service.name="voice-gateway"
deployment.environment="home"
device_id="xiaoai-speaker"
session_id="s_..."
conversation_id="c_..."
turn_id="t_..."
```

模块 attributes：

```text
asr.engine
asr.text_length
asr.empty
hermes.model
hermes.base_url_host
tts.engine
tts.voice
playback_id
playback.url_host
device.command
```

不要写入 trace：

```text
API key / token
原始音频
声纹 embedding
完整用户原话
完整 Hermes 响应
```

用户原话可以用短期日志保留；trace 中只保留 `text_length`、`text_hash`、`language` 或意图摘要。

## 6. Log 关联

所有结构化事件日志都应包含：

```text
trace_id
span_id
turn_id
conversation_id
```

这样 Grafana 中的排障路径是：

```text
Dashboard alert
  -> 找到异常时间窗口
  -> 打开相关 trace
  -> 看到失败 span
  -> 通过 trace_id 跳转到 Loki 日志
```

反查 LogQL：

```logql
{service="voice-gateway", log_type="events"} | json | trace_id="4bf92f..."
```

## 7. 失败状态

失败 span 应设置 OpenTelemetry status 为 `ERROR`，并写入：

```text
error_type
error
recoverable
last_successful_stage
failed_stage
```

`voice.turn` root span 也应带最终结果：

```text
turn.status="completed" | "failed" | "cancelled" | "timeout"
failed_stage="hermes"
failure_reason="timeout"
last_successful_stage="asr"
```

这样即使不翻日志，也能在 trace view 中看到断点。

当 trace 上报失败或 Tempo 暂不可用时，`turn.failed` 日志事件必须保留同样字段，作为链路断点定位的兜底。

`turn.completed` 和 `turn.failed` 还必须保留 `stage_ms` 与 `slowest_stage`，作为 trace 不可用时定位慢环节的兜底：

```text
total_ms=18200
stage_ms.hermes=14500
slowest_stage="hermes"
```

## 8. 部署约定

建议端口：

```text
Tempo: http://127.0.0.1:3200
Alloy OTLP gRPC: http://127.0.0.1:4317
Alloy OTLP HTTP: http://127.0.0.1:4318
Grafana: http://127.0.0.1:3300
```

建议配置目录：

```text
voice-gateway/ops/tempo/
voice-gateway/ops/alloy/
voice-gateway/ops/grafana/
```

建议数据目录：

```text
voice-gateway/.ops-data/tempo/
```

`.ops-data` 应加入 `.gitignore`。

## 9. 第一阶段落地

第一阶段只做最小 trace：

```text
voice.turn
  asr
  hermes
  tts
  playback
```

每个 span 先只记录耗时、状态、错误类型和核心 ID。等主链路稳定后，再补 audio、VAD、speaker identity、barge-in 和 tool call。

## 10. 验收标准

- Grafana 中能看到 `voice.turn` trace。
- 一轮请求的 `asr`、`hermes`、`tts`、`playback` 至少有独立 span。
- Hermes/TTS/playback 失败时，对应 span 标记为 error。
- root span 能显示 `failed_stage`、`failure_reason` 和 `last_successful_stage`。
- 日志中包含 `trace_id`，能从 Tempo trace 跳到 Loki 日志。
- trace 中不包含 API key、原始音频、声纹 embedding 和完整用户原话。
