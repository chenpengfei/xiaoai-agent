# 05 声纹识别

本文定义声纹识别如何接入 `voice-gateway`，用于家庭成员识别、个性化记忆和权限控制。

上级索引：[Voice Gateway 总设计](./DESIGN.md)  
前置文档：[03 连续对话](./03-continuous-conversation.md)  
相关文档：[07 安全与隐私](./07-security-privacy.md)

## 1. 阶段目标

为每轮用户语音附加身份信息：

```text
AudioWindow
  -> speaker embedding
  -> profile matching
  -> SpeakerIdentity
  -> HermesTurn
```

声纹识别不应阻塞最小闭环，也不应成为普通问答的硬依赖。

## 2. 声纹数据结构

```python
SpeakerProfile:
    speaker_id: str
    display_name: str
    embedding_version: str
    embeddings: list[list[float]]
    permissions: list[str]

SpeakerIdentity:
    speaker_id: str | None
    display_name: str | None
    confidence: float
    status: "identified" | "unknown" | "ambiguous"
```

## 3. 识别时机

声纹识别使用 VAD 切出的完整 `AudioWindow`。

推荐顺序：

```text
SpeechEnded
  -> ASR
  -> SpeakerIdentity
  -> HermesTurn
```

如果声纹慢，可以先发起 ASR，同时并行计算 speaker embedding。Hermes 调用前等待一个短超时，超时则以 `unknown` 继续。

## 4. profile 管理

每个家庭成员有一个 profile：

```text
profiles/
  chen_pengfei.json
  meng_yixing.json
```

一个 profile 应包含多段 embedding，避免单样本不稳。

profile 存储格式由 gateway 定义，不直接采用某个模型库的私有格式。这样未来可以替换 sherpa-onnx 或 FunASR。

## 5. 权限策略

身份分为：

```text
identified
unknown
ambiguous
```

策略：

- `identified`：允许个性化记忆、家庭成员称呼、低风险个人化回答。
- `unknown`：允许普通问答，但不写入个人记忆，不执行高权限动作。
- `ambiguous`：允许普通问答，需要二次确认后才能写入记忆或执行敏感动作。

## 6. Hermes 上下文

HermesTurn 应包含：

```python
speaker: SpeakerIdentity
```

示例语义：

```text
当前说话人：陈鹏飞，置信度 0.86。
如果本轮涉及个人记忆，可以按陈鹏飞身份处理。
```

不应把原始声纹 embedding 发给 Hermes。

## 7. 候选引擎

候选：

- sherpa-onnx speaker identification。
- FunASR CAM++。

初期只做 speaker identification，不做复杂 diarization。多人同时说话、交叠语音和说话人分离是后续增强。

## 8. 与其他阶段的关系

- 最小闭环不依赖声纹：[02 最小闭环](./02-minimal-loop.md)
- 连续对话可以先没有声纹：[03 连续对话](./03-continuous-conversation.md)
- 自然打断可使用声纹降低误触发：[04 自然打断](./04-barge-in.md)
- 声纹隐私策略见 [07 安全与隐私](./07-security-privacy.md)
