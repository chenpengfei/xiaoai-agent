# Voice Gateway Speaker Client

这是运行在小爱音箱上的 Rust client。它只负责设备侧转发和被动执行指令，业务状态机、VAD、ASR、Hermes、TTS 和可观测性都在 Mac Mini 的 `voice-gateway` 中完成。

刷机、固件 patch、SSH 开启等前置步骤不由本目录维护。需要刷机时，请参考原始 [open-xiaoai GitHub 仓库](https://github.com/idootop/open-xiaoai)。

## 职责

- 连接 `voice-gateway` WebSocket/RPC server。
- 上传 `record` PCM 音频流。
- 响应 `start_recording`、`stop_recording`、`start_play`、`stop_play`、`run_shell`。
- 上报播放、录音、KWS 等设备侧事件。
- 断线后自动重连。

## 音箱路径

默认安装到：

```text
/data/voice-gateway/client
/data/voice-gateway/server.txt
/data/voice-gateway/init.sh
/data/voice-gateway/boot.sh
```

`/data/init.sh` 可以指向或复制 `boot.sh`，用于开机启动。

## 构建

在 `voice-gateway` 根目录运行：

```shell
./scripts/build-speaker-client.sh
```

默认目标是 `armv7-unknown-linux-gnueabihf`，产物路径：

```text
client/client-rust/target/armv7-unknown-linux-gnueabihf/release/client
```

如果只想在当前 Mac 上做语法和类型检查，可以在本目录运行：

```shell
cargo check
```

## 安装

确认音箱已完成刷机且可以 SSH 后，在 `voice-gateway` 根目录运行：

```shell
VOICE_GATEWAY_SPEAKER_HOST=192.168.1.23 \
VOICE_GATEWAY_SERVER_URL=ws://192.168.1.9:4399 \
./scripts/install-speaker-client.sh
```

如果需要脚本自动输入密码，可安装 `sshpass` 并设置：

```shell
VOICE_GATEWAY_SPEAKER_PASSWORD=open-xiaoai
```

没有 `sshpass` 时，脚本会走普通 `ssh` / `scp`，由终端提示输入密码。

## 只更新 Server 地址

```shell
VOICE_GATEWAY_SPEAKER_HOST=192.168.1.23 \
VOICE_GATEWAY_SERVER_URL=ws://192.168.1.9:4399 \
./scripts/configure-speaker-client.sh
```

## 手工运行

```shell
ssh -o HostKeyAlgorithms=+ssh-rsa root@192.168.1.23
mkdir -p /data/voice-gateway
echo 'ws://192.168.1.9:4399' > /data/voice-gateway/server.txt
/data/voice-gateway/client ws://192.168.1.9:4399
```
