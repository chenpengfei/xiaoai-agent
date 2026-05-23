# 06 Grafana / Loki / Tempo / Alloy 可观测性栈

本文定义 `voice-gateway` 的可观测性技术栈落地约定，覆盖日志查询、链路追踪、Dashboard 和告警入口。链路追踪设计见 [04 链路追踪](./04-tracing.md)。

上级索引：[4 Ops 运维设计](./README.md)  
相关文档：[02 日志系统](./02-logging.md)、[03 指标系统](./03-metrics.md)、[05 告警系统](./05-alerting.md)

## 1. 选型结论

采用：

```text
Grafana Alloy
  -> 采集 Mac Mini 本机日志文件

Grafana Loki
  -> 存储日志，提供 LogQL 查询

Grafana Tempo
  -> 存储 trace，提供单次请求链路查看

Grafana
  -> 人工翻阅日志、Trace View、Dashboard、告警入口
```

不在第一版引入 ELK / OpenSearch。它们功能更重，但对当前家庭 Mac Mini 单机运维来说维护成本偏高。

## 2. 目标架构

```text
voice-gateway/logs/events.jsonl
voice-gateway/logs/runtime.log
voice-gateway/logs/audit.jsonl
  -> Grafana Alloy file tail
  -> Loki labels + log line
  -> Grafana Explore

voice-gateway OTLP traces
  -> Grafana Alloy OTLP receiver
  -> Tempo
  -> Grafana Trace View

voice-gateway metrics
  -> Grafana Alloy / Prometheus-compatible scrape
  -> Grafana Dashboard / Alerting
```

第一版先要求能稳定 tail 本地文件并在 Grafana Explore 中查询；随后接入 OTLP trace 到 Tempo；Dashboard 和告警在日志与 trace 接入稳定后再逐步补齐。

## 3. 日志文件映射

```text
voice-gateway/logs/events.jsonl
  log_type="events"
  用于结构化事件查询、trace 复盘、日志型告警和指标派生。

voice-gateway/logs/runtime.log
  log_type="runtime"
  用于人读运行日志、启动诊断、音频 probe 和脚本输出。

voice-gateway/logs/audit.jsonl
  log_type="audit"
  用于设备命令、安全事件、配置变更和人工恢复动作审计。
```

当前最小闭环还只有混合日志：

```text
voice-gateway/logs/voice-gateway-minimal.log
```

在日志拆分完成前，Alloy 也应采集这个文件：

```text
voice-gateway/logs/voice-gateway-minimal.log
  log_type="minimal"
```

## 4. Label 设计

低基数 label：

```text
service="voice-gateway"
env="home"
host="<mac-mini-hostname>"
log_type="events" | "runtime" | "audit" | "minimal"
device_id="xiaoai-speaker"
```

不要作为 Loki label：

```text
trace_id
span_id
turn_id
conversation_id
playback_id
request_id
user_text
error
```

这些字段基数太高，应该保留在 JSON log body 中，通过 `| json` 后过滤。`trace_id` 不做 Loki label，但必须保留在日志 body 中，用于从 Tempo trace 反查 Loki 日志。

## 5. Grafana Explore 常用查询

按 turn 复盘：

```logql
{service="voice-gateway", log_type="events"} | json | turn_id="t_xxx"
```

按 conversation 复盘：

```logql
{service="voice-gateway", log_type="events"} | json | conversation_id="c_xxx"
```

看最近错误：

```logql
{service="voice-gateway", log_type="events"} | json | level="error"
```

看失败事件：

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

看慢请求最慢环节：

```logql
{service="voice-gateway", log_type="events"} | json | event=~"turn\\.(completed|failed)" | line_format "{{.slowest_stage}} {{.total_ms}}ms"
```

看音频流 probe：

```logql
{service="voice-gateway", log_type=~"runtime|minimal"} |= "record stream bytes_total="
```

看一次播放链路：

```logql
{service="voice-gateway", log_type="events"} | json | playback_id="p_xxx"
```

从 Tempo trace 反查日志：

```logql
{service="voice-gateway", log_type="events"} | json | trace_id="4bf92f..."
```

## 6. 第一版部署约定

建议服务端口：

```text
Grafana: http://127.0.0.1:3300
Loki:    http://127.0.0.1:3100
Tempo:   http://127.0.0.1:3200
Alloy:   http://127.0.0.1:12345
OTLP:    http://127.0.0.1:4317 / http://127.0.0.1:4318
```

服务健康检查：

```text
Grafana health
  -> Grafana UI 可访问，datasource 可查询 Loki / Tempo

Loki health
  -> 能查询最近 2 分钟 voice-gateway 日志

Tempo health
  -> 能查询最近 voice.turn trace

Alloy health
  -> file tail 和 OTLP receiver 正常

Discord health
  -> contact point test 可发送到 #xiaoai-alerts
```

建议配置目录：

```text
voice-gateway/ops/grafana/
voice-gateway/ops/loki/
voice-gateway/ops/tempo/
voice-gateway/ops/alloy/
```

建议数据目录：

```text
voice-gateway/.ops-data/grafana/
voice-gateway/.ops-data/loki/
voice-gateway/.ops-data/tempo/
```

`.ops-data` 应加入 `.gitignore`，配置文件可以入库。

## 7. Alloy 采集要求

Alloy 至少要做这些事：

- tail `voice-gateway/logs/*.log` 和 `voice-gateway/logs/*.jsonl`。
- 给不同文件打 `log_type` label。
- 把日志推送到本机 Loki。
- 接收 `voice-gateway` 上报的 OTLP trace。
- 把 trace 推送到本机 Tempo。

事件日志是 JSONL，但不要把高基数字段提升为 Loki label。查询时用 LogQL `| json` 解析字段。

## 8. Grafana 使用约定

Grafana 里至少创建三个入口：

```text
Explore: Voice Gateway Logs
  用于临时查询和排障。

Trace View: Voice Gateway Traces
  用于查看一轮 voice turn 断在哪个 span。

Dashboard: Voice Gateway Overview
  用于日常巡检。

Alerting: Voice Gateway Alerts
  用于 P1/P2 告警规则。
```

常用查询应保存为 Grafana Explore starred queries 或 dashboard links，避免每次手写 LogQL。

## 9. 验收标准

- Grafana Explore 能看到 `minimal` 或 `events` 日志。
- 能用 `turn_id` 查询一轮完整链路。
- 能查询 `hermes.completed` 且看到 `latency_ms`。
- 能查询 `turn.completed` / `turn.failed` 且看到 `stage_ms` 和 `slowest_stage`。
- 能查询 `record stream bytes_total=` 判断音频流是否推进。
- Grafana Trace View 能看到 Tempo 中的 `voice.turn` trace。
- 能基于 Loki 配置至少一条测试告警。
- Discord contact point 测试发送成功。
- Grafana 能从 trace 跳到同 `trace_id` 的 Loki 日志。
- Loki label 中不包含 `turn_id`、`conversation_id` 等高基数字段。
- 日志 body 中保留 `trace_id`，能从 trace 跳转到相关日志。
