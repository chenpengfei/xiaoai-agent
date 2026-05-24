# 5 步骤：TTS 播放效果对比

本文记录 PoC 阶段对 Mac Mini 生成音频播放和小米音箱原生文字播报的听感对比结论。

## 1. 目标

验证同一段较长中文文本，在小爱音箱 Pro LX06 上分别通过两条路径播放时，哪条路径更适合作为 Hermes 回答的默认 TTS 输出。

对比路径：

```text
Edge TTS:
  Mac Mini edge-tts
  -> mp3
  -> Mac Mini HTTP URL
  -> 音箱 miplayer 播放

小米原生 TTS:
  音箱 /usr/sbin/tts_play.sh
  -> /tmp/tts/*.mp3
  -> 音箱本地播放
```

## 2. 测试文本

```text
日薄西山，气息奄奄。人命危浅，朝不虑夕。别人能给你的，别人也能从你这里拿走。大张旗鼓未必会有成绩，可逢场作戏也未必不是真心
```

这段文本包含短句、成语和较长现代句，适合观察停顿、气口、韵律和长文本稳定性。

## 3. Edge TTS 验证

Mac Mini 先生成 Edge TTS 音频：

```bash
uvx edge-tts \
  --voice zh-CN-XiaoxiaoNeural \
  --rate '+0%' \
  --text '日薄西山，气息奄奄。人命危浅，朝不虑夕。别人能给你的，别人也能从你这里拿走。大张旗鼓未必会有成绩，可逢场作戏也未必不是真心' \
  --write-media voice-gateway/audio-samples/tts/ab-test/l-edge-long-contrast.mp3
```

确保 Mac Mini 暴露音频目录：

```bash
cd ~/projects/vibe-coding/xiaoai-agent/voice-gateway/audio-samples/tts
python3 -m http.server 8765 --bind 0.0.0.0
```

音箱播放命令：

```bash
ssh -tt \
  -o HostKeyAlgorithms=+ssh-rsa \
  -o PubkeyAcceptedAlgorithms=+ssh-rsa \
  -o StrictHostKeyChecking=accept-new \
  root@192.168.1.2 \
  'miplayer -f "http://192.168.1.9:8765/ab-test/l-edge-long-contrast.mp3"'
```

音频参数：

```text
duration: 14.62s
sample_rate: 24000 Hz
channels: mono
bitrate: 48 kbps
```

## 4. 小米原生 TTS 验证

音箱直接播放文字：

```bash
ssh -tt \
  -o HostKeyAlgorithms=+ssh-rsa \
  -o PubkeyAcceptedAlgorithms=+ssh-rsa \
  -o StrictHostKeyChecking=accept-new \
  root@192.168.1.2 \
  '/usr/sbin/tts_play.sh "日薄西山，气息奄奄。人命危浅，朝不虑夕。别人能给你的，别人也能从你这里拿走。大张旗鼓未必会有成绩，可逢场作戏也未必不是真心"'
```

验证时小米原生 TTS 在音箱上生成了临时文件：

```text
/tmp/tts/tts_12c20af68b3b39bc0747d40c88f0ced1_1779590936_338949.mp3
```

音频参数：

```text
duration: 12.85s
sample_rate: 16000 Hz
channels: mono
bitrate: 32 kbps
```

脚本退出时出现：

```text
/usr/sbin/tts_play.sh: line 1: my_log: not found
```

但音频已经正常生成并播放完成；该错误不影响本次听感验证。

## 5. 对比结论

实听结论：

```text
Edge TTS 播放效果明显好于小米原生 TTS 播放。
```

主要差异：

- Edge TTS 的中文长句韵律更自然。
- Edge TTS 的停顿和气口更接近正常朗读。
- Edge TTS 的音色更舒服，长文本听感更稳定。
- 小米原生 TTS 语速更紧，采样率和码率更低，整体质感弱于 Edge。

## 6. PoC 决策

Hermes 回答默认不走小米原生 `tts_play.sh`，而采用 Mac Mini 生成音频 URL 后让音箱播放。

当前推荐：

```text
默认 TTS: Edge TTS
输出格式: mp3
播放方式: miplayer 播放 Mac Mini HTTP URL
运行时策略: 不保留其他 TTS 引擎、fallback 或缓存方案
```

小米原生 `tts_play.sh` 仅作为本文件里的历史对比证据保留，不进入 `voice-gateway` 运行时。
