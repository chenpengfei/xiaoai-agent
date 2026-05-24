# Voice Gateway Device Components

本目录只保存刷机之后运行在小爱音箱上的组件。刷机、固件 patch、分区切换和 SSH 开启不在 `voice-gateway` 中维护，需要时请参考原始 [open-xiaoai GitHub 仓库](https://github.com/idootop/open-xiaoai)。

## Components

- `client-rust/`：音箱端 WebSocket/RPC client，负责音频流、播放、shell RPC 和设备事件转发。
- `kws/`：设备端轻量 KWS 脚本、关键词生成器和配置模板。

## Device Paths

默认音箱路径：

```text
/data/voice-gateway/client
/data/voice-gateway/server.txt
/data/voice-gateway/init.sh
/data/voice-gateway/boot.sh
/data/voice-gateway/kws/
```

## Scripts

从 `voice-gateway` 根目录使用：

```shell
./scripts/build-speaker-client.sh
./scripts/install-speaker-client.sh
./scripts/configure-speaker-client.sh
./scripts/install-speaker-kws.sh
./scripts/validate-speaker-e2e.sh
```
