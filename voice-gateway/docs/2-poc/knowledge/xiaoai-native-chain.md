# 小爱音箱原生链路架构

本文记录小爱音箱原生语音链路的核心理解，以及它和当前 Hermes 链路的边界。

当前项目的主链路已经不是小米云 ASR fallback；Hermes 走音箱本地 KWS / VAD、`record` stream、Mac Mini sherpa-onnx VAD/STT、Mac Mini TTS URL 和音箱播放。项目原始动机见 [1-idea](../../1-idea/README.md)。本文只保留有助于理解原生链路和两条链路边界的内容。

## 原生链路

小米原生链路大致如下：

```text
用户说“小爱同学”
  -> 音箱本地 KWS / 唤醒词检测
  -> 原生 mibrain/AIVS 进入录音会话
  -> 音箱采集唤醒后的用户语音
  -> 小米云 ASR：语音 -> 文本
  -> 小米云 NLP / 技能 / 对话处理
  -> 小米云返回 ASR 结果、播报、播放或控制指令
  -> 音箱执行指令或播放回答
```

“小爱同学”的唤醒通常在音箱本地完成。具体实现可能是低功耗唤醒模块、DSP、系统服务或本地模型；小米这部分是闭源实现，当前项目不能断言具体模型文件或算法。可以确定的是：唤醒后上传给小米云 ASR 的是语音音频，不是文本。

## 云端返回

小米云返回给音箱的内容不是单一类型，而是按阶段下发：

```text
ASR 阶段：
  -> 识别文本和元数据

NLP / 技能阶段：
  -> 意图、控制指令、播报指令或播放调度

TTS / 播放阶段：
  -> 待播报文本、音频资源 URL、播放列表或其它可播放资源
```

所以，原生链路里“返回给音箱的是文本还是语音”没有单一答案：

- ASR 中间结果是文本和识别元数据。
- 普通问答可能返回回答文本或播报指令，再由音箱原生 TTS/播报系统播放。
- 音乐、有声内容等场景可能返回音频资源 URL 或播放列表，由音箱拉流播放。

## 本地可见事件

小米云 ASR 识别完成后，原生运行时会收到 `SpeechRecognizer.RecognizeResult` 这类事件。音箱本地会把这些 instruction 事件写入：

```text
/tmp/mico_aivs_lab/instruction.log
```

这些事件对原生运行时主要用于状态同步、日志、调试、UI/联动展示，以及本地模块协作。历史上 Mac Mini 曾经旁路读取这里的 `RecognizeResult.text` 作为 Hermes fallback 输入；当前实现已经移除这条路径。

## Hermes 链路边界

当前 Hermes 链路和小米原生链路并行：

```text
“小爱同学”
  -> 小米原生链路

“你好 <问题>”（当前本地触发方式）
  -> 音箱本地 KWS / VAD
  -> 音箱 record stream
  -> Mac Mini sherpa-onnx VAD / STT
  -> Hermes
  -> Mac Mini TTS URL
  -> 音箱播放
```

当前边界：

```text
Mac Mini Server 不上传音频到小米云 ASR。
Mac Mini Server 不直接调用小米云 ASR API。
Mac Mini Server 不再读取小米云 ASR 文本触发 Hermes。
Mac Mini Server 不在 4-* 主链路里主动 abort 小爱原生回答。
Hermes 由 record stream 本地 STT 触发。
3-* 小米云 ASR 路线只作为 legacy route / manual rollback 保留。
```

如果“你好”和“小爱同学”被同时或误触发，当前策略是接受两条链路可能同时回答，不再引入抢播和打断状态机。Mac Mini 侧命中“你好”后不再发送原生 wake event，而是通过音箱文本播报随机播放“我在 / 在 / 诶”。
