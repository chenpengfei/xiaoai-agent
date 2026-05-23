# 05 告警系统

本文定义 `xiaoai-agent` 的告警原则、规则、分级和通知策略。

上级索引：[4 Ops 运维设计](./README.md)  
相关文档：[03 指标系统](./03-metrics.md)、[04 链路追踪](./04-tracing.md)、[07 运维 Runbook](./07-ops-runbook.md)

## 1. 告警原则

告警只为需要人介入的异常发声。家庭语音系统会有自然噪声、偶发空 ASR、短时网络抖动，这些不应该立刻打扰人。

告警入口采用 Grafana Alerting。第一阶段优先基于 Loki 日志查询触发告警；后续有 `/metrics` 后，再把高频指标型规则迁移到指标查询。

规则：

- 对持续异常告警，不对单个失败告警。
- 对用户可感知失败告警，不对内部自动恢复告警。
- 每条告警都必须有对应 runbook。
- 告警内容必须包含设备、时间窗口、症状、最近错误事件和排查入口。

## 2. 分级

```text
P1
  系统完全不可用，最小闭环无法响应，需要立即处理。

P2
  主要功能退化，用户能感知明显失败或延迟，需要尽快处理。

P3
  趋势异常或容量风险，可以在方便时处理。
```

第一阶段默认只启用 P1/P2。P3 可以先进入每日摘要，避免早期规则太吵。

## 3. 告警规则

进程不可用：

```text
condition:
  voice_gateway_up == 0 for 60s
severity:
  P1
message:
  voice-gateway 进程不可用
runbook:
  07-ops-runbook.md#1-voice-gateway-进程不可用
```

音频流中断：

```text
condition:
  voice_gateway_audio_last_seen_age_seconds > 30 for 2m
severity:
  P1
message:
  音箱 record stream 超过 2 分钟无音频
```

连续 turn 失败：

```text
condition:
  increase(voice_gateway_turn_failure_total[10m]) >= 3
severity:
  P2
message:
  最近 10 分钟连续语音请求失败
```

Hermes 持续失败：

```text
condition:
  increase(voice_gateway_hermes_failure_total[10m]) >= 3
severity:
  P2
message:
  Hermes 调用持续失败
```

TTS 持续失败：

```text
condition:
  increase(voice_gateway_tts_failure_total[10m]) >= 3
severity:
  P2
message:
  TTS 生成持续失败
```

播放失败：

```text
condition:
  increase(voice_gateway_playback_failure_total[10m]) >= 3
severity:
  P2
message:
  音箱播放命令持续失败
```

端到端延迟过高：

```text
condition:
  p95(voice_gateway_turn_duration_ms[15m]) > 15000
severity:
  P2
message:
  语音请求 p95 延迟超过 15 秒
```

单阶段持续变慢：

```text
condition:
  p95(voice_gateway_turn_stage_duration_ms{stage="hermes"}[15m]) > 10000
severity:
  P2
message:
  Hermes 阶段 p95 延迟超过 10 秒
```

日志停写：

```text
condition:
  voice_gateway_event_log_last_write_age_seconds > 120
severity:
  P2
message:
  事件日志超过 2 分钟没有更新
```

磁盘空间不足：

```text
condition:
  disk_free_percent < 10
severity:
  P2
message:
  Mac Mini 日志或音频输出磁盘空间不足
```

日志型规则示例：

```logql
count_over_time({service="voice-gateway", log_type="events"} | json | event="runtime.worker.failed" [5m]) > 0
```

```logql
count_over_time({service="voice-gateway", log_type="events"} | json | event="hermes.failed" [10m]) >= 3
```

```logql
count_over_time({service="voice-gateway", log_type="events"} | json | event="playback.failed" [10m]) >= 3
```

```logql
count_over_time({service="voice-gateway", log_type="runtime"} |= "record stream bytes_total=" [2m]) == 0
```

慢请求规则示例：

```logql
count_over_time({service="voice-gateway", log_type="events"} | json | event=~"turn\\.(completed|failed)" | total_ms > 15000 [10m]) >= 3
```

运维栈自身也需要告警：

```text
condition:
  Loki 最近 2 分钟没有收到 voice-gateway 日志
severity:
  P2
message:
  Loki 日志摄入中断
```

```text
condition:
  Tempo 最近 10 分钟没有收到 voice.turn trace，且期间有 turn 事件
severity:
  P2
message:
  Tempo trace 摄入中断
```

```text
condition:
  Discord contact point test failed or notification delivery failed
severity:
  P2
message:
  Discord 告警发送失败
```

## 4. 通知渠道

第一阶段统一发送到 Discord。Grafana Alerting 使用 Discord webhook contact point，按严重级别路由到不同频道或同一频道的不同 mention 策略。

```text
P1
  Discord immediate notification
  mention: @here or explicit maintainer role

P2
  Discord grouped notification
  mention: none by default

P3
  Discord daily summary or no immediate notification
```

建议频道：

```text
#xiaoai-alerts
  P1/P2 告警和恢复通知

#xiaoai-ops-digest
  P3 趋势、每日摘要和低优先级容量提醒
```

Webhook 配置原则：

- Discord webhook URL 只保存在 Grafana secret / 本机环境变量中，不写入仓库。
- 每个 contact point 使用明确名称，例如 `discord-xiaoai-alerts`。
- P1 和 P2 可以使用同一个 webhook，但在 message template 中明确 `severity`。
- 告警恢复也发送到 Discord，方便看到故障闭环。

家庭场景下，夜间静默策略很重要：

- P1 仍通知。
- P2 在夜间发送到 Discord 但不 mention，早晨摘要。
- 同一规则 30 分钟内只提醒一次，除非状态恢复后再次触发。

## 5. 告警内容模板

Discord 消息模板：

```text
{{ if eq .Status "firing" }}[FIRING]{{ else }}[RESOLVED]{{ end }} [{{ .CommonLabels.severity }}] {{ .CommonLabels.alertname }}

device: {{ .CommonLabels.device_id }}
service: {{ .CommonLabels.service }}
window: {{ .StartsAt }} .. {{ .EndsAt }}
summary: {{ .CommonAnnotations.summary }}
last_error: {{ .CommonAnnotations.last_error }}
last_turn_id: {{ .CommonAnnotations.last_turn_id }}
trace: {{ .CommonAnnotations.trace_url }}
logs: {{ .CommonAnnotations.logs_url }}
runbook: {{ .CommonAnnotations.runbook_url }}
```

示例：

```text
[FIRING] [P2] Hermes 调用持续失败
device: xiaoai-speaker
service: voice-gateway
window: 2026-05-22 21:10:00 .. 2026-05-22 21:20:00
summary: 10 分钟内 hermes.failed=3
last_error: timeout after 90s
last_turn_id: t_...
trace: http://127.0.0.1:3300/explore?...trace_id=...
logs: http://127.0.0.1:3300/explore?...turn_id=t_...
runbook: voice-gateway/docs/4-ops/07-ops-runbook.md#3-hermes-持续失败
```

P1 可以在消息开头增加 `@here` 或维护者 role mention。P2/P3 默认不 mention，避免家庭运行时噪声过高。

## 6. 恢复通知

每条告警应有恢复条件：

```text
voice_gateway_up == 1 for 2m
audio_last_seen_age_seconds < 10 for 2m
turn_success_total increased
hermes_failure_total no increase for 10m
```

恢复通知用于确认系统回到可用状态，也方便记录故障窗口。

## 7. 验收标准

- 进程退出、音频流中断、Hermes/TTS/播放持续失败能触发告警。
- Grafana Alerting 中有基于 Loki 的第一批日志告警规则。
- Grafana Alerting 配置 Discord webhook contact point。
- P1/P2 告警和恢复通知可以发送到 Discord。
- Loki/Tempo/Alloy/Grafana/Discord 这套运维栈自身故障也能被发现。
- 偶发单次 ASR 空文本不会触发 P1/P2。
- 告警文本包含 runbook 和最近相关事件。
- 告警有抑制和恢复逻辑。
- 每条已启用告警都能被人工演练触发一次。
