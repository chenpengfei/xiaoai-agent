# Voice Gateway Device KWS

这是刷机后可安装到小爱音箱上的轻量关键词唤醒能力，基于 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)。刷机、固件 patch、SSH 开启等前置步骤不由本目录维护，需要时请参考原始 [open-xiaoai GitHub 仓库](https://github.com/idootop/open-xiaoai)。

## 音箱路径

默认安装到：

```text
/data/voice-gateway/kws/
```

关键文件：

```text
keywords.txt    自定义唤醒词
reply.txt       唤醒后的提示语或提示音 URL，可选
kws             KWS 主程序 artifact
monitor         KWS 事件转发程序 artifact
models/         KWS 模型 artifact
```

KWS artifact 不从旧 open-xiaoai release 隐式下载。需要部署 KWS 二进制和模型时，把 artifact 放到本机目录，然后通过 `VOICE_GATEWAY_KWS_ARTIFACT_DIR` 指向它。

## 生成关键词

在本目录运行：

```shell
uv run keywords.py --tokens tokens.txt --output keywords.txt --text my-keywords.txt
```

`my-keywords.txt` 每行一个中文唤醒词。

## 安装

在 `voice-gateway` 根目录运行：

```shell
VOICE_GATEWAY_SPEAKER_HOST=192.168.1.23 \
VOICE_GATEWAY_KWS_ARTIFACT_DIR=/path/to/kws-artifact \
./scripts/install-speaker-kws.sh
```

artifact 目录需要至少包含：

```text
kws
monitor
models/encoder.onnx
models/decoder.onnx
models/joiner.onnx
models/tokens.txt
```

如果只想下发关键词和启动脚本，可以不设置 `VOICE_GATEWAY_KWS_ARTIFACT_DIR`；音箱端启动时会检查模型和二进制是否已存在。

## 欢迎语

`reply.txt` 每行一条，支持文字、HTTP URL 和 `file://` 本地音频：

```text
主人你好，请问有什么吩咐？
https://example.com/music.wav
file:///usr/share/sound-vendor/AiNiRobot/wakeup_ei_01.wav
```

## 调试

安装 artifact 后，可在音箱上运行：

```shell
sh /data/voice-gateway/kws/debug.sh
```
