# xiaoai-agent

This repository contains the `voice-gateway` runtime plus shared artifacts.

## Runtime Projects

- `voice-gateway/`: Mac Mini side voice gateway. It owns the long-running gateway process, observability stack, local ASR/VAD integration, Hermes calls, TTS generation, and speaker playback control.

Firmware patching and upstream XiaoAI reference material live outside this repository. When needed, refer to the archived upstream project: <https://github.com/idootop/open-xiaoai>.

## Shared Artifacts

`models/` is a repository-level shared artifact directory. It is intentionally outside `voice-gateway/`.

Current shared models:

```text
models/sherpa-onnx-paraformer-zh-2024-03-09/
models/sherpa-onnx-paraformer-zh-small-2024-03-09/
```

These directories provide sherpa-onnx ASR model files such as `model.int8.onnx` and `tokens.txt`. Projects may reference them through environment variables, for example:

```sh
VOICE_GATEWAY_SHERPA_MODEL_DIR=/path/to/xiaoai-agent/models/sherpa-onnx-paraformer-zh-2024-03-09
SHERPA_ONNX_MODEL_DIR=/path/to/xiaoai-agent/models/sherpa-onnx-paraformer-zh-2024-03-09
```

Shared artifacts are data dependencies, not project dependencies. Adding a model under `models/` must not introduce imports, package references, or startup requirements on external upstream source checkouts.
