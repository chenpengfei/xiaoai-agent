# 08 音箱端 Client 与 KWS

本文定义 `voice-gateway` 对音箱设备侧能力的边界：只接管刷机之后的运行侧组件，包括音箱端 client、设备端 KWS、安装配置脚本和端到端验证。

上级索引：[Voice Gateway 总设计](./DESIGN.md)

## 1. 边界

`voice-gateway` 不迁移、不封装、不维护刷机和固件 patch 工具。涉及刷机、固件 patch、分区切换、SSH 开启等前置步骤时，文档只指引用户去原始 [open-xiaoai GitHub 仓库](https://github.com/idootop/open-xiaoai) 查看和执行。

刷机完成之后，`voice-gateway` 负责：

- 构建或管理音箱端 Rust client。
- 安装 client 到音箱的 `/data/voice-gateway/client`。
- 写入 `server.txt`，让 client 连接 Mac Mini 上的 `voice-gateway` WebSocket/RPC server。
- 安装和配置设备端 KWS。
- 验证音箱连接、录音、播放、KWS 事件和完整语音链路。

这里的“独立”指运行和运维阶段不需要进入 `open-xiaoai` 工程目录，不依赖它的 Python 包、Rust crate、虚拟环境或脚本。

## 2. 目录

目标目录：

```text
voice-gateway/
  client/
    README.md
    client-rust/
      Cargo.toml
      Cargo.lock
      src/
      init.sh
      boot.sh
    kws/
      README.md
      init.sh
      boot.sh
      debug.sh
      keywords.py
      tokens.txt
      my-keywords.txt
  scripts/
    build-speaker-client.sh
    install-speaker-client.sh
    configure-speaker-client.sh
    install-speaker-kws.sh
    validate-speaker-e2e.sh
```

根目录 `models/` 继续作为两个工程之外的共享 artifact，不迁入 `client/`。

## 3. 音箱端 Client

音箱端 client 是 `voice-gateway` 与音箱之间的设备侧 agent。它负责：

- 连接 Mac Mini 的 WebSocket/RPC server。
- 上传 `record` PCM 音频流。
- 接收 `start_recording`、`start_play`、`stop_play`、`run_shell` 等 RPC。
- 调用设备侧 `arecord` / `miplayer` / shell 能力。
- 上报连接、录音、播放、KWS 和错误事件。

目标路径：

```text
voice-gateway/client/client-rust
```

验收标准：

- Rust crate 不依赖 `open-xiaoai`。
- 与 `server.adapters.xiaoai_ws_server` 的协议互通。
- 支持配置 server 地址。
- 支持断线重连。
- 支持安装到 `/data/voice-gateway/client`。
- 支持开机自启动。

## 4. 设备端 KWS

设备端 KWS 是刷机后可选安装的轻量唤醒能力。它用于未来降低 Mac Mini 侧持续 VAD/ASR 压力，或者给音箱端提供更低延迟的本地唤醒信号。

目标路径：

```text
voice-gateway/client/kws
```

验收标准：

- 安装路径统一为 `/data/voice-gateway/kws`。
- 关键词文件可以由 Mac Mini 侧生成和下发。
- KWS 命中事件通过 client 上报给 `voice-gateway`。
- KWS 失败时不影响基础 record stream 链路。
- KWS 二进制和模型作为 artifact 管理，不从旧 open-xiaoai release 隐式下载。

## 5. 安装与配置

目标脚本：

```text
scripts/build-speaker-client.sh
scripts/install-speaker-client.sh
scripts/configure-speaker-client.sh
scripts/install-speaker-kws.sh
scripts/validate-speaker-e2e.sh
```

安装流程：

```text
确认音箱已按 open-xiaoai 指引刷机并可 SSH
  -> 构建或选择 client binary
  -> 上传 client 到 /data/voice-gateway/client
  -> 上传 init.sh / boot.sh
  -> 写入 /data/voice-gateway/server.txt
  -> 可选安装 KWS artifact 和关键词配置
  -> 启动 client
  -> 验证 voice-gateway 侧连接和链路日志
```

验收标准：

- Mac Mini IP 改变后可以一键更新 server 地址。
- 安装脚本可重复执行。
- 安装失败时不会破坏已有 client。
- 验证脚本能确认音箱 SSH、client 进程、server 配置和 KWS 配置。

## 6. 协议兼容

`voice-gateway` 已经拥有自己的 Python WebSocket/RPC server。设备端 client 必须面向该 server 做兼容。

协议边界：

```text
Stream(tag="record", bytes=pcm)
Request(command="run_shell" | "start_recording" | "start_play" | "stop_play")
Response(id=..., data=...)
Event(event=..., data=...)
```

验收标准：

- 协议有单元测试。
- 设备端 client 和 Python server 有集成测试或手工 runbook。
- 协议变更必须同时更新 server、client 和文档。

## 7. 非目标

当前不把以下内容纳入 `voice-gateway`：

- 刷机工具封装。
- 固件 patch 工具链。
- 固件下载、提取、重打包和分区切换。
- 单独维护交叉编译 runtime 镜像。
- `https://github.com/idootop/open-xiaoai/tree/main/examples/xiaozhi` 的 Python/Rust server。
- xiaozhi 云端协议和小智 AI 对话逻辑。
- MiGPT、Gemini、stereo 等示例工程。

刷机相关内容由原始 [open-xiaoai GitHub 仓库](https://github.com/idootop/open-xiaoai) 承担；`voice-gateway` 只在刷机完成后的运行侧接管。

## 8. 迁移顺序

建议顺序：

```text
1. 迁移 speaker client 源码和启动脚本
2. 修改 crate 名、安装路径和下载策略
3. 增加 build/install/configure/validate 脚本
4. 迁移 KWS 脚本、关键词生成器和配置
5. 建立 KWS artifact 管理方式
6. 用已刷机音箱做端到端验证
```
