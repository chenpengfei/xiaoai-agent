# 4 Ops 运维设计

本目录定义最小闭环跑通后，`xiaoai-agent` 的日志、监控、告警和排障体系。目标不是一次性引入复杂平台，而是先把“能看见、能定位、能被叫醒”做成稳定闭环。

## 1. 运维目标

运维系统需要回答四类问题：

```text
系统现在还活着吗？
一次语音请求卡在哪里？
失败会不会自动恢复？
什么时候需要人介入？
```

当前最小闭环的主链路是：

```text
小爱音箱 record stream
  -> voice-gateway runtime
  -> wake-word ASR
  -> question ASR
  -> Hermes
  -> TTS
  -> speaker playback
```

因此第一阶段运维重点是：

- 日志：保留可查询的结构化事件，能按 `conversation_id` / `turn_id` 复盘。
- 监控：从日志和进程状态派生核心指标，展示链路健康度和延迟。
- 告警：只对需要介入的持续异常发声，避免把偶发 ASR 空文本当作事故。
- Runbook：每个告警都能对应到明确检查步骤和恢复动作。

可观测性系统统一采用 Grafana 生态：

```text
logs
  -> Grafana Alloy
  -> Grafana Loki
  -> Grafana Explore

metrics
  -> Grafana Alloy / Prometheus-compatible scrape
  -> Grafana Dashboard / Alerting

traces
  -> OpenTelemetry SDK
  -> Grafana Alloy
  -> Grafana Tempo
  -> Grafana Trace View
```

其中 Loki 负责日志存储和 LogQL 查询，Tempo 负责 trace 存储和链路查看，Grafana 负责人工翻阅、Dashboard、Trace View 和告警入口。Alloy 作为本机采集/转发组件，负责把日志、指标和 trace 送入对应后端。

## 2. 文档顺序

建议按下面顺序阅读和实现：

1. [01 可观测性总设计](./01-observability.md)
2. [02 日志系统](./02-logging.md)
3. [03 指标系统](./03-metrics.md)
4. [04 链路追踪](./04-tracing.md)
5. [05 告警系统](./05-alerting.md)
6. [06 Grafana / Loki / Tempo / Alloy 可观测性栈](./06-grafana-loki-tempo-alloy.md)
7. [07 运维 Runbook](./07-ops-runbook.md)

## 3. 分阶段落地

```text
Phase 0: JSONL 事件日志
  -> 当前 JsonLineEventLogger 和 run script 日志文件

Phase 1: 本机可查询运维
  -> 日志拆分、Grafana Alloy 采集、Loki 存储、Grafana Explore 查询、常见故障 runbook

Phase 2: 指标导出
  -> 从事件日志派生 counters/gauges/histograms，提供 Prometheus textfile 或 HTTP /metrics，并接入 Grafana Dashboard

Phase 3: Trace 接入
  -> OpenTelemetry spans、Alloy OTLP receiver、Tempo、trace to logs 关联

Phase 4: Dashboard 与告警
  -> Grafana Dashboard、Grafana Alerting、Loki recording/alert rules、trace drilldown

Phase 5: 长期治理
  -> 脱敏、留存、容量、SLO、事故复盘
```

第一阶段必须足够简单：Mac Mini 本机单进程运行时，只需要本地日志、派生指标和轻量告警即可支撑日常运维。等连续对话、自然打断和多设备接入稳定后，再升级到完整监控栈。

## 4. 目录边界

`voice-gateway/docs/3-design` 继续保存业务能力设计；本目录保存运行期运维设计。可观测性已经从阶段设计迁移到这里，因为它横跨最小闭环、连续对话、打断、TTS、播放和设备接入。
