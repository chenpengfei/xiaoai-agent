# xiaoai-agent

This repository contains two independent runtime projects plus shared artifacts.

## Runtime Projects

- `voice-gateway/`: Mac Mini side voice gateway. It owns the long-running gateway process, observability stack, local ASR/VAD integration, Hermes calls, TTS generation, and speaker playback control.
- `open-xiaoai/`: XiaoAI firmware/client reference and device-side tooling. It remains an independent project and is not imported by `voice-gateway`.

The two projects may speak the same XiaoAI WebSocket/RPC protocol at runtime, but neither project should depend on the other's Python package, Rust crate, virtual environment, scripts, or source tree.

## Shared Artifacts

`models/` is a repository-level shared artifact directory. It is intentionally outside both `voice-gateway/` and `open-xiaoai/`.

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

Shared artifacts are data dependencies, not project dependencies. Adding a model under `models/` must not introduce imports, package references, or startup requirements between `voice-gateway/` and `open-xiaoai/`.
