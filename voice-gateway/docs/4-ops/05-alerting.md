# 05 告警系统

本文定义 `xiaoai-agent` 的告警原则、规则、分级和通知策略。

上级索引：[4 Ops 运维设计](./README.md)  
相关文档：[03 指标系统](./03-metrics.md)、[04 链路追踪](./04-tracing.md)、[07 运维 Runbook](./07-ops-runbook.md)

## 1. 告警原则

告警只为需要人介入的异常发声。这个系统目前是个人音箱场景，统计趋势类告警价值不高，默认只关注两类问题：

- 服务功能是否正常：进程、音频流、事件日志、核心 worker、单次 turn/Hermes/TTS/播放失败。
- 单次性能是否明显异常：一轮完整链路超过阈值，或 Hermes/TTS/播放某个阶段单次超过阈值。

告警入口采用 Grafana Alerting，通过 Prometheus 指标规则触发，再统一发送到 Discord。

规则：

- 不做统计类告警，例如最近 10 分钟失败次数、成功率、p95 延迟、趋势退化。
- 对单次明确失败告警，方便个人及时知道刚才那次链路是否坏了。
- 对单次明显慢告警，方便及时定位性能退化。
- `DatasourceNoData` 不作为业务告警发送到 Discord；单次事件规则没有数据时视为 OK。
- 对用户可感知失败告警，不对内部自动恢复告警。
- 每条告警都必须有对应 runbook。
- 告警内容必须包含设备、症状和排查入口。

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

### 3.1 服务功能类

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
  voice_gateway_audio_chunk_total > 0 and voice_gateway_audio_last_seen_age_seconds > 120 for 2m
severity:
  P1
message:
  音箱 record stream 超过 2 分钟无音频
```

runtime worker 单次失败：

```text
condition:
  sum(increase(voice_gateway_runtime_worker_failure_total[5m])) > 0
severity:
  P1
message:
  voice-gateway 后台 worker 失败
```

单次 turn 失败：

```text
condition:
  sum(increase(voice_gateway_turn_failure_total[5m])) > 0
severity:
  P2
message:
  本次语音请求失败
```

Hermes 单次失败：

```text
condition:
  sum(increase(voice_gateway_hermes_failure_total[5m])) > 0
severity:
  P2
message:
  Hermes 调用失败
```

TTS 单次失败：

```text
condition:
  sum(increase(voice_gateway_tts_failure_total[5m])) > 0
severity:
  P2
message:
  TTS 生成失败
```

播放单次失败：

```text
condition:
  sum(increase(voice_gateway_playback_failure_total[5m])) > 0
severity:
  P2
message:
  音箱播放命令失败
```

日志停写：

```text
condition:
  event log age bool result > 0 for 2m
severity:
  P2
message:
  事件日志超过 2 分钟没有更新
```

### 3.2 单次性能类

端到端单次过慢：

```text
condition:
  sum(increase(voice_gateway_turn_slow_total[5m])) > 0
severity:
  P2
message:
  单次语音请求超过 15 秒
```

单阶段单次过慢：

```text
condition:
  sum(increase(voice_gateway_stage_slow_total[5m])) > 0
severity:
  P2
message:
  Hermes/TTS/播放某个阶段单次超过阈值
```

阶段慢阈值：

```text
turn total > 15000ms
hermes > 10000ms
tts > 5000ms
playback > 10000ms
```

Dashboard 可以继续保留成功率、p50/p95、失败趋势等统计面板用于观察，但这些统计面板默认不触发 Discord。

这里的 `[5m]` 只是为了让 Prometheus 在当前 scrape/remote_write 节奏下有足够样本计算 `increase()`；业务语义仍然是“最近 5 分钟内出现过一次明确失败或一次明显变慢”，不是统计趋势告警。

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
