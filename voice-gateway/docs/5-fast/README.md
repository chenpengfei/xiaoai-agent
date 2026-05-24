# 5 Fast 性能优化

本目录保存 `voice-gateway` 的性能优化设计。目标不是盲目追求单点极限，而是沿着真实语音链路逐段降低延迟，并且每一项优化都能被日志、指标和 trace 验证。

## 1. 文档顺序

1. [01 全链路优化路径](./01-optimization-roadmap.md)
2. [02 Edge TTS 方案](./02-edge-tts.md)

## 2. 优化边界

性能优化文档聚焦运行期体验：

```text
唤醒 / 收音
  -> ASR
  -> Hermes
  -> TTS
  -> 音箱播放
```

运维系统本身的日志、指标、trace 和告警设计仍放在 [4-ops](../4-ops/README.md)。
