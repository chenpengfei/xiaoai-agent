# 2 路径：搞定 LX06 音箱侧 Open-XiaoAI 链路

## 定位

`2-*` 系列重点解决音箱侧：让小爱音箱 Pro `LX06` 进入可维护状态，安装并维护 open-xiaoai client，并验证音箱可以稳定连接 Mac Mini 上的 server。

`3-*` 系列重点解决服务侧：把 Mac Mini 上当前已经跑通的 xiaozhi 示例 server，改造成真正接入 Hermes 的完整 server。

## 2 系列结论

当前这台小爱音箱 Pro `LX06`、固件 `1.94.13` 的音箱侧链路已经验证可行：

```text
小爱音箱 Pro LX06
  -> open-xiaoai patched firmware
  -> SSH / root access
  -> /data/open-xiaoai/client
  -> WebSocket: ws://192.168.1.9:4399
  -> Mac Mini 上的 xiaozhi 示例 server
  -> 音箱播报测试内容
```

这说明：音箱侧已经具备后续服务侧接入 Hermes 的基础。

## 为什么选择这条路径

- `LX06` 是 `open-xiaoai` 明确支持的型号。
- 已验证当前这台 LX06 可以刷入 `LX06_1.94.13_patched.squashfs` 并正常启动。
- 已验证 SSH 可用，可以安装和维护音箱端 client。
- 已验证音箱端 client 能稳定连接 Mac Mini 上的 WebSocket server。
- 已验证 Mac Mini server 能向音箱下发指令并触发音箱播报。
- 这条路径比轮询小爱云端对话记录更接近“直接使用音箱的麦克风和扬声器”。

## 2 系列分阶段

### 2.1 让 LX06 进入可维护状态

文档：`2.1-lx06-firmware-ssh.md`

目标：

- 确认设备、固件、IP、分区状态。
- 保持 `system0=patched`、`system1=original`。
- 确认 SSH 登录稳定可用。

状态：已完成。

### 2.2 安装和维护音箱端 open-xiaoai client

文档：`2.2-speaker-client.md`

目标：

- 在音箱 `/data/open-xiaoai` 下安装 client。
- 设置 server 地址为 Mac Mini。
- 配置 `/data/init.sh` 让 client 开机自启动。

状态：已完成。

### 2.3 用示例 server 验证音箱侧 client 能连通 Mac Mini

文档：`2.3-mac-mini-server.md`

目标：

- 在 Mac Mini 上监听 `4399`。
- 用 xiaozhi Docker 示例跑通音箱 client 到 server 的闭环。
- 验证 server 可以触发音箱播报。

状态：已完成示例闭环。

说明：这个步骤仍放在 2 系列，是因为它服务于“音箱侧 client 是否能连通 server”的验证。真正的服务侧改造放到 3 系列。

### 2.4 端到端验证和运维检查

文档：`2.4-end-to-end-validation.md`

目标：

- 验证音箱 SSH、client、Mac Mini server、WebSocket 连接。
- 验证 server 能触发音箱播报。
- 记录排障入口。

状态：已完成最小闭环验证。

## 3 系列入口

音箱侧完成后，后续进入服务侧：

- `3-server-hermes-path.md`：3 系列总览，Mac Mini 服务侧接入 Hermes。
- `3.1-hermes-entry.md`：确认 Hermes 调用入口。
- `3.2-xiaozhi-protocol.md`：梳理 xiaozhi 示例 server 协议。
- `3.3-minimal-hermes-loop.md`：实现最小 Hermes 闭环。
- `3.4-stt-tts-runtime.md`：STT / TTS / 运行方式。

## 当前固定事实

- 音箱型号：小爱音箱 Pro `LX06`
- 音箱固件：`1.94.13`
- 音箱真实 IP：`192.168.1.2`
- 音箱 MAC：`50:88:11:6f:f2:a8`
- SSH 用户：`root`
- SSH 密码：`open-xiaoai`
- Mac Mini 当前 IP：`192.168.1.9`
- WebSocket server：`ws://192.168.1.9:4399`
- `192.168.1.10` 不是这台音箱

## 当前固件文件

```text
/Users/chenpengfei/projects/vibe-coding/xiaoai-agent/LX06_1.94.13.squashfs
/Users/chenpengfei/projects/vibe-coding/xiaoai-agent/LX06_1.94.13_patched.squashfs
```

当前不再使用强制 SSH 排障固件；该临时文件已经删除。
