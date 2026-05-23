# 03 指标系统

本文定义最小闭环后的指标、采集方式、Dashboard 和健康检查。

上级索引：[4 Ops 运维设计](./README.md)  
相关文档：[01 可观测性总设计](./01-observability.md)、[02 日志系统](./02-logging.md)

## 1. 目标

指标系统用于持续判断：

- `voice-gateway` 进程是否存活。
- 音箱音频流是否持续进入 Mac Mini。
- 每轮交互是否能走到播放完成。
- ASR、Hermes、TTS、播放的延迟是否退化。
- 失败是否集中在某个模块。

监控不直接替代日志。指标负责看趋势和触发告警，日志负责复盘原因。

## 2. 采集方式

日志、Dashboard 和告警入口采用 Grafana 体系：

```text
events.jsonl / runtime.log / OTLP traces / metrics
  -> Grafana Alloy
  -> Loki / Tempo / metrics backend
  -> Grafana Explore / Trace View / Dashboard / Alerting
```

指标采集分两层：

- 第一层：从 Loki 日志查询派生简单趋势，例如错误数、慢请求、成功率。
- 第二层：`voice-gateway` 或独立 sidecar 提供 Prometheus 格式 `/metrics`，由 Grafana Alloy 或后续 Prometheus/Mimir 采集。

推荐演进顺序：

1. 先接入 Alloy + Loki + Grafana，让人能查日志。
2. 用 Loki LogQL 做第一批 dashboard 面板和日志告警。
3. 再增加 `/metrics` 或 Prometheus textfile，补齐 counters/gauges/histograms。

在 `/metrics` 尚未完成前，Dashboard 上的成功率、失败数和慢请求先用 Loki LogQL 从 `events.jsonl` 派生；进程存活、磁盘空间等主机级指标可以先用本机脚本或 Alloy/Prometheus 兼容采集补齐。

## 3. 核心指标

进程指标：

```text
voice_gateway_up
voice_gateway_uptime_seconds
voice_gateway_restart_total
voice_gateway_runtime_worker_failure_total
voice_gateway_event_log_last_write_age_seconds
```

音频流指标：

```text
voice_gateway_audio_chunk_total
voice_gateway_audio_bytes_total
voice_gateway_audio_last_seen_age_seconds
voice_gateway_audio_gap_total
voice_gateway_audio_rms
voice_gateway_audio_peak
```

链路指标：

```text
voice_gateway_turn_total
voice_gateway_turn_success_total
voice_gateway_turn_failure_total
voice_gateway_turn_duration_ms
voice_gateway_turn_stage_duration_ms
voice_gateway_asr_latency_ms
voice_gateway_hermes_latency_ms
voice_gateway_tts_latency_ms
voice_gateway_playback_latency_ms
voice_gateway_turn_slowest_stage_count
```

模型和外部依赖指标：

```text
voice_gateway_asr_empty_total
voice_gateway_hermes_failure_total
voice_gateway_hermes_timeout_total
voice_gateway_tts_failure_total
voice_gateway_playback_failure_total
```

状态指标：

```text
voice_gateway_device_connected
voice_gateway_dialogue_state
voice_gateway_runtime_state
voice_gateway_queue_depth
voice_gateway_ops_stack_up
voice_gateway_loki_ingest_last_seen_age_seconds
voice_gateway_tempo_trace_ingest_total
voice_gateway_discord_notification_failure_total
```

## 4. 事件到指标映射

```text
runtime.state_changed
  -> voice_gateway_runtime_state

runtime.worker.failed
  -> voice_gateway_runtime_worker_failure_total

audio.chunk.received
  -> voice_gateway_audio_chunk_total
  -> voice_gateway_audio_bytes_total
  -> voice_gateway_audio_last_seen_age_seconds

audio.stream.gap
  -> voice_gateway_audio_gap_total

asr.completed
  -> voice_gateway_asr_latency_ms
  -> voice_gateway_asr_empty_total when normalized_text is empty

hermes.completed
  -> voice_gateway_hermes_latency_ms

hermes.failed
  -> voice_gateway_hermes_failure_total

tts.completed
  -> voice_gateway_tts_latency_ms

playback.finished
  -> voice_gateway_turn_success_total

playback.failed
  -> voice_gateway_playback_failure_total

error.recovered
  -> voice_gateway_turn_failure_total or voice_gateway_recovery_total

turn.failed
  -> voice_gateway_turn_failure_total
  -> voice_gateway_turn_duration_ms from total_ms
  -> voice_gateway_turn_stage_duration_ms from stage_ms
  -> voice_gateway_turn_slowest_stage_count by slowest_stage

turn.completed
  -> voice_gateway_turn_success_total
  -> voice_gateway_turn_duration_ms from total_ms
  -> voice_gateway_turn_stage_duration_ms from stage_ms
  -> voice_gateway_turn_slowest_stage_count by slowest_stage
```

当前 `playback.finished` 代表播放命令被接受并完成当前管理器调用；后续接入真实播放状态后，应区分 `playback.command_accepted` 和 `playback.device_finished`。

## 5. Dashboard

第一版 Dashboard 分四块：

```text
Service
  up, uptime, restarts, event log age

Audio
  last audio age, bytes/sec, rms/peak, gap count

Conversation
  turns/min, success rate, end-to-end latency p50/p95, slowest stage distribution

Dependencies
  ASR latency, Hermes latency, TTS latency, playback failures
```

建议默认时间窗口：

- 近 15 分钟：排查当前问题。
- 近 6 小时：看家庭日常使用稳定性。
- 近 7 天：看模型、网络和播放失败趋势。

Grafana 中至少保留三个入口：

- Explore：面向临时排障，按 `turn_id`、`conversation_id`、`event`、关键词翻日志。
- Trace View：面向单次请求链路定位，查看 `voice.turn` 下每个 span 的状态和耗时。
- Dashboard：面向日常巡检，展示成功率、延迟、错误数和音频流活跃度。

## 6. 健康检查

需要两个层次：

```text
liveness
  进程活着，事件日志仍能写入

readiness
  ASR 模型可用，Hermes 可访问，TTS 输出目录可写，音箱连接可用
```

最小本机检查可以是：

```text
check process exists
check log updated in last N seconds
check record stream probe advanced in last N seconds
check Hermes /v1 endpoint reachable
check TTS HTTP server reachable
```

长期应由 `voice-gateway` 提供本地 HTTP health endpoint：

```text
GET /health/live
GET /health/ready
GET /metrics
```

## 7. 验收标准

- 能看到进程存活、音频流活跃度和最后事件时间。
- 能看到最近 15 分钟 turn 成功率。
- 能看到 ASR、Hermes、TTS、播放的延迟分布。
- 任一核心依赖失败时，指标能定位到模块。
- 运维栈自身的 Loki/Tempo/Alloy/Grafana/Discord 健康状态可见。
- Dashboard 上的每个异常数字都能跳回日志查询。
