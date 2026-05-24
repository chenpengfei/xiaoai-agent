# 01 全链路优化路径

本文定义 `voice-gateway` 的性能优化路线。优化顺序以真实体验为准：先定位慢在哪一段，再逐段降低延迟，并用日志、指标和 trace 确认优化是否生效。

上级索引：[5 Fast 性能优化](./README.md)  
相关文档：[4 Ops 运维设计](../4-ops/README.md)

## 1. 目标

最终目标是让一次完整语音请求的主链路稳定、可解释、可优化：

```text
小爱音箱唤醒
  -> 收音 / 端点检测
  -> ASR
  -> Hermes
  -> TTS
  -> 音箱播放
```

每一次完整链路都应能回答：

```text
总耗时是多少？
最慢环节是哪一段？
每段分别耗时多少？
慢是偶发、持续，还是由某类输入触发？
```

## 2. 当前基线

最近一次完整链路的日志显示：

```text
turn_id: t_ebb199d52bee4b019542eefc69bc021b
trace_id: 3536496115f74f4faf28df7a764cfb88
text: 一加一等于几
response: 一加一等于二。
total_ms: 13610
stage_ms:
  asr: 86
  hermes: 9006
  tts: 2353
  playback: 2161
  tts_playback_total: 4516
slowest_stage: hermes
```

这个基线说明：

- ASR 当前不是主要瓶颈。
- Hermes 是当前最大瓶颈，但先放到最后优化。
- TTS 和 playback 合计约 4.5 秒；经实听验证后，TTS 方案固定为 Edge TTS，后续优先优化播放链路。
- 单次分析必须结合日志和 trace；如果 Tempo 缺少历史 trace，则先用 Loki 事件日志复盘。

## 3. 优化优先级

第一阶段按下面顺序推进：

```text
P0: 可观测性基线
  -> 已完成

P1: TTS 方案评估
  -> 已完成，固定 Edge TTS

P2: 播放链路优化
  -> 待优化

P3: ASR / 端点检测细调
  -> 待观察，不作为当前瓶颈

P4: Hermes 调用优化
  -> 最后优化
```

这样做的原因是：Hermes 虽然最慢，但 TTS 是独立边界，改动风险较低；验证后 Edge TTS 的效果最好，运行时不再保留其他 TTS 方案。

## 4. 优化状态

| 环节 | 当前状态 | 优化状态 | 下一步 |
| --- | --- | --- | --- |
| 日志 | 已有结构化事件日志 | 已完成 | 继续补充性能字段 |
| 指标 | 已接入 Grafana Dashboard | 已完成 | 保留 TTS latency / failure 指标 |
| Trace | 已设计并接入 Tempo | 基础完成 | 确保新链路都写入 spans |
| ASR | 最近链路约 86ms | 暂不优化 | 持续观察 p95 |
| Hermes | 最近链路约 9006ms | 待优化 | TTS 后再处理 |
| TTS | EdgeTTS 生成 MP3，本地模型音色/速度未超过 Edge | 已固定 Edge TTS | 保持单一 Edge 链路 |
| Playback | 最近约 2161ms | 待优化 | 验证 wav/mp3、HTTP 拉取和音箱播放耗时 |

## 5. 验证方法

每一轮优化都用同一组方法验证：

```text
Loki:
  看单次 turn.completed 的 total_ms、stage_ms、slowest_stage

Metrics:
  看 turn duration、stage duration、success/failure、audio last seen

Trace:
  看单次 trace 的 span waterfall，确认瓶颈是否转移
```

关键 LogQL 查询：

```logql
{service="voice-gateway", log_type="events"} | json | event=~"turn\\.(completed|failed)" | line_format "{{.turn_id}} {{.slowest_stage}} {{.total_ms}}ms {{.stage_ms}}"
```

优化是否生效，不看单次感觉，而看：

- `turn_duration_ms` 的 p50 / p95 是否下降。
- `tts` 阶段 p95 是否下降。
- `playback` 阶段是否被新格式影响。
- 失败率是否上升。

## 6. 当前阶段

当前阶段的 TTS 结论：

```text
主方案: edge_tts
本地模型: 已从运行时删除
缓存/fallback: 已从运行时删除
Hermes: 暂不优化
```

详细方案见 [02 Edge TTS 方案](./02-edge-tts.md)。
