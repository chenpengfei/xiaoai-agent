# 02 Edge TTS 方案

本文记录 TTS 方案收敛后的运行时决策：`voice-gateway` 只使用 Edge TTS 生成 MP3，再通过 Mac Mini HTTP URL 让音箱播放。经过 `2-poc/5-tts-quality-comparison.md` 的实听验证，Edge TTS 的中文长句韵律、停顿和音色明显好于小米原生 TTS；本地 TTS 模型的听感或速度也没有超过 Edge，因此不再保留运行时切换、fallback 或本地模型实验入口。固定短提示可以进入文件缓存，避免重连时重复生成。

上级索引：[5 Fast 性能优化](./README.md)  
相关设计：[06 TTS 与播放控制](../3-design/06-tts-playback.md)  
对比结论：[5 TTS 播放效果对比](../2-poc/5-tts-quality-comparison.md)

## 当前链路

```text
Hermes text
  -> EdgeTTSFileEngine
  -> 常用短提示缓存命中则复用 mp3
  -> python -m edge_tts
  -> mp3 文件
  -> PlaybackResource(url=..., format=mp3)
  -> 小爱音箱 miplayer 播放
```

保留的配置只有：

```bash
VOICE_GATEWAY_TTS_OUTPUT_DIR=audio-samples/tts
VOICE_GATEWAY_TTS_HTTP_BASE_URL=http://127.0.0.1:8765
VOICE_GATEWAY_TTS_VOICE=zh-CN-XiaoxiaoNeural
VOICE_GATEWAY_TTS_RATE=+0%
```

不再支持：

- `VOICE_GATEWAY_TTS_ENGINE`
- `VOICE_GATEWAY_TTS_FORMAT`
- `VOICE_GATEWAY_TTS_FALLBACK_ENGINE`
- `VOICE_GATEWAY_TTS_MODEL_DIR`
- `VOICE_GATEWAY_TTS_CACHE_*`
- 本地 TTS 可选依赖和 fallback 链路

当前内置固定短提示缓存：“我在”“在”“诶”“已连接”。启动时会预加载这些短句，缓存文件放在 `VOICE_GATEWAY_TTS_OUTPUT_DIR/cache/`，文件名包含音色、语速和文本的哈希；普通 Hermes 回答仍按请求生成独立 mp3。

唤醒反馈和“已连接”提示是硬缓存命中策略：播放时只允许读取已经预热好的 mp3。如果缓存文件不存在，本次提示直接失败并记录错误，不在唤醒现场临时调用 Edge TTS。

## 交互参数

对齐 Microsoft Speech 交互式识别的常用参数：

```bash
VOICE_GATEWAY_QUESTION_TIMEOUT_SECONDS=5
VOICE_GATEWAY_SILERO_MIN_SILENCE=0.5
```

含义：

- 唤醒后最多等待用户问题 `5s`，对齐常见 initial silence timeout。
- 用户说话后检测到 `0.5s` 静音即判定一句话结束，对齐常见 segmentation silence timeout。
- `VOICE_GATEWAY_ACK_SUPPRESSION_SECONDS=0`。`miplayer` 播放已被 await，默认不额外丢弃反馈后的音频，避免快语速用户的句首被切掉；如实测存在明显回采，再临时调高。

## 可观测性

TTS 事件保留运行期必需字段：

```text
tts.engine=edge
tts.model=<voice>
tts.format=mp3
tts.latency_ms
tts.text_chars
tts.local_path
```

指标保留：

```text
voice_gateway_tts_latency_ms
voice_gateway_tts_failure_total
```

后续优化优先看 Edge TTS 生成耗时和音箱播放耗时。若需要进一步降低首播延迟，优先评估流式播放或播放链路优化，不再恢复多 TTS 引擎切换。
