#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICE_GATEWAY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$VOICE_GATEWAY_DIR/.." && pwd)"
LOG_DIR="$VOICE_GATEWAY_DIR/logs"
LOG_FILE="$LOG_DIR/voice-gateway-minimal.log"
EVENTS_LOG_FILE="$LOG_DIR/events.jsonl"

# 补充本机常用可执行文件路径，确保 uv、edge-tts 等命令能被脚本直接找到。
export PATH="/Users/chenpengfei/.local/bin:/opt/homebrew/bin:$PATH"
# 将 voice-gateway 源码目录加入 Python 模块路径，确保始终使用本项目代码启动。
export PYTHONPATH="$VOICE_GATEWAY_DIR${PYTHONPATH:+:$PYTHONPATH}"
# 指定 uv 缓存目录，避免运行时依赖用户主目录下的默认缓存位置。
export UV_CACHE_DIR="${UV_CACHE_DIR:-/private/tmp/uv-cache}"

# Hermes 服务的环境变量文件路径，用于读取本地 API_SERVER_KEY。
export HERMES_ENV_PATH="${HERMES_ENV_PATH:-/Users/chenpengfei/.hermes/.env}"
# Hermes OpenAI 兼容接口地址，voice-gateway 会把用户问题发送到这里。
export VOICE_GATEWAY_OPENAI_BASE_URL="${VOICE_GATEWAY_OPENAI_BASE_URL:-http://127.0.0.1:8642/v1}"
# Hermes OpenAI 兼容接口使用的模型名。
export VOICE_GATEWAY_OPENAI_MODEL="${VOICE_GATEWAY_OPENAI_MODEL:-hermes-agent}"
# Hermes 请求超时时间，单位为秒。
export VOICE_GATEWAY_OPENAI_TIMEOUT="${VOICE_GATEWAY_OPENAI_TIMEOUT:-90}"
# Mac Mini 新增链路的唤醒词，不影响小米音箱原生“小爱同学”链路。
export VOICE_GATEWAY_WAKE_WORD="${VOICE_GATEWAY_WAKE_WORD:-你好}"
# voice-gateway 自己监听小爱音箱补丁连接的地址。
export VOICE_GATEWAY_HOST="${VOICE_GATEWAY_HOST:-0.0.0.0}"
export VOICE_GATEWAY_PORT="${VOICE_GATEWAY_PORT:-4399}"
# 唤醒后等待用户正式提问的最长时间，单位为秒。
export VOICE_GATEWAY_QUESTION_TIMEOUT_SECONDS="${VOICE_GATEWAY_QUESTION_TIMEOUT_SECONDS:-5}"
# 播放唤醒提示短语后额外忽略采集音频的时长。miplayer 已被 await，默认不再丢弃句首。
export VOICE_GATEWAY_ACK_SUPPRESSION_SECONDS="${VOICE_GATEWAY_ACK_SUPPRESSION_SECONDS:-0}"
# ACK 前缀剥离后少于该字符数则认为不是有效问题，继续等待用户提问。
export VOICE_GATEWAY_MIN_QUESTION_TEXT_CHARS="${VOICE_GATEWAY_MIN_QUESTION_TEXT_CHARS:-2}"
# sherpa-onnx 中文 ASR 模型目录，需包含 model.int8.onnx 和 tokens.txt。
export VOICE_GATEWAY_SHERPA_MODEL_DIR="${VOICE_GATEWAY_SHERPA_MODEL_DIR:-$PROJECT_ROOT/models/sherpa-onnx-paraformer-zh-2024-03-09}"
# Silero VAD 模型文件路径，用于从音箱音频流中切分用户语音。
export VOICE_GATEWAY_SILERO_VAD_MODEL="${VOICE_GATEWAY_SILERO_VAD_MODEL:-$VOICE_GATEWAY_DIR/config/silero_vad.onnx}"
# TTS 生成音频的本地输出目录，供 HTTP 服务暴露给音箱播放。
export VOICE_GATEWAY_TTS_OUTPUT_DIR="${VOICE_GATEWAY_TTS_OUTPUT_DIR:-$VOICE_GATEWAY_DIR/audio-samples/tts}"
# TTS 音频 HTTP 基础地址，音箱会通过该地址拉取生成后的音频文件。
export VOICE_GATEWAY_TTS_HTTP_BASE_URL="${VOICE_GATEWAY_TTS_HTTP_BASE_URL:-http://192.168.1.9:8765}"
# Edge TTS 音色和语速。TTS 运行时固定使用 Edge TTS 并输出 mp3。
export VOICE_GATEWAY_TTS_VOICE="${VOICE_GATEWAY_TTS_VOICE:-zh-CN-XiaoxiaoNeural}"
export VOICE_GATEWAY_TTS_RATE="${VOICE_GATEWAY_TTS_RATE:-+0%}"
# Silero VAD 判定语音活动的阈值，数值越高越保守。
export VOICE_GATEWAY_SILERO_VAD_THRESHOLD="${VOICE_GATEWAY_SILERO_VAD_THRESHOLD:-0.45}"
# Silero VAD 认为一句话结束所需的最短静音时长，单位为秒。
export VOICE_GATEWAY_SILERO_MIN_SILENCE="${VOICE_GATEWAY_SILERO_MIN_SILENCE:-0.5}"
# Silero VAD 接受一次语音片段的最短语音时长，单位为秒。
export VOICE_GATEWAY_SILERO_MIN_SPEECH="${VOICE_GATEWAY_SILERO_MIN_SPEECH:-0.12}"
# 送入 VAD/ASR 前的音频增益，单位为 dB，用于提升远场音箱采集音量。
export VOICE_GATEWAY_VAD_GAIN_DB="${VOICE_GATEWAY_VAD_GAIN_DB:-30}"
# 普通运行日志最低输出级别。默认保留关键状态和错误，过滤 DEBUG 噪声。
export VOICE_GATEWAY_LOG_LEVEL="${VOICE_GATEWAY_LOG_LEVEL:-INFO}"
# 结构化事件日志最低记录级别。events.jsonl 是 Grafana/Loki 的主数据源，默认保留 INFO/WARN/ERROR。
export VOICE_GATEWAY_EVENT_LEVEL="${VOICE_GATEWAY_EVENT_LEVEL:-INFO}"
# 音频探测日志最低输出级别。默认 WARN 会抑制 record stream 探测，排查音频时临时改为 INFO 或 DEBUG。
export VOICE_GATEWAY_AUDIO_PROBE_LEVEL="${VOICE_GATEWAY_AUDIO_PROBE_LEVEL:-WARN}"
# 是否抑制底层音频分片日志，1 表示减少噪声日志输出。
export VOICE_GATEWAY_SUPPRESS_AUDIO_CHUNKS="${VOICE_GATEWAY_SUPPRESS_AUDIO_CHUNKS:-1}"
# 结构化事件 JSONL 输出路径，用于 Loki / Grafana 查询和指标派生。
export VOICE_GATEWAY_EVENTS_LOG_FILE="${VOICE_GATEWAY_EVENTS_LOG_FILE:-$EVENTS_LOG_FILE}"
# Prometheus-compatible metrics endpoint，供 ops stack 抓取。
export VOICE_GATEWAY_METRICS_ENABLED="${VOICE_GATEWAY_METRICS_ENABLED:-1}"
export VOICE_GATEWAY_METRICS_HOST="${VOICE_GATEWAY_METRICS_HOST:-127.0.0.1}"
export VOICE_GATEWAY_METRICS_PORT="${VOICE_GATEWAY_METRICS_PORT:-9109}"
# OpenTelemetry trace 导出到 Alloy OTLP HTTP receiver。未安装 OTel 依赖时自动降级为日志 trace_id/span_id。
export VOICE_GATEWAY_OTEL_ENABLED="${VOICE_GATEWAY_OTEL_ENABLED:-1}"
export VOICE_GATEWAY_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="${VOICE_GATEWAY_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT:-http://127.0.0.1:4318/v1/traces}"
# 音频探测日志的字节间隔，用于观察持续采集是否仍在工作。
export VOICE_GATEWAY_PROBE_INTERVAL_BYTES="${VOICE_GATEWAY_PROBE_INTERVAL_BYTES:-160000}"
# VAD 命中语音前保留的预卷音频时长，避免切掉句首，单位为秒。
export VOICE_GATEWAY_VAD_PRE_ROLL_SECONDS="${VOICE_GATEWAY_VAD_PRE_ROLL_SECONDS:-0.8}"

if [[ -z "${VOICE_GATEWAY_OPENAI_API_KEY:-}" && -f "$HERMES_ENV_PATH" ]]; then
  API_SERVER_KEY_FROM_ENV="$(
    python3 - "$HERMES_ENV_PATH" <<'PY'
from pathlib import Path
import sys
for raw_line in Path(sys.argv[1]).read_text().splitlines():
    line = raw_line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    name, value = line.split('=', 1)
    if name.strip() == 'API_SERVER_KEY':
        print(value.strip().strip('"').strip("'"))
        break
PY
  )"
  if [[ -n "$API_SERVER_KEY_FROM_ENV" ]]; then
    # Hermes OpenAI 兼容接口鉴权密钥，默认从 HERMES_ENV_PATH 中的 API_SERVER_KEY 派生。
    export VOICE_GATEWAY_OPENAI_API_KEY="$API_SERVER_KEY_FROM_ENV"
  fi
fi

mkdir -p "$LOG_DIR"

# 之后脚本自身和 voice-gateway 子进程的 stdout/stderr 都同时输出到终端和日志文件。
# 这样用户可以实时看终端，Hermes 也可以实时读取 LOG_FILE 进行诊断。
exec > >(tee -a "$LOG_FILE") 2>&1

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found. Please install uv or add it to PATH." >&2
  exit 1
fi

if [[ ! -f "$VOICE_GATEWAY_SHERPA_MODEL_DIR/model.int8.onnx" || ! -f "$VOICE_GATEWAY_SHERPA_MODEL_DIR/tokens.txt" ]]; then
  echo "ERROR: missing sherpa ASR model files under $VOICE_GATEWAY_SHERPA_MODEL_DIR" >&2
  exit 1
fi

if [[ ! -f "$VOICE_GATEWAY_SILERO_VAD_MODEL" ]]; then
  echo "ERROR: missing Silero VAD model at $VOICE_GATEWAY_SILERO_VAD_MODEL" >&2
  exit 1
fi

ONNXRUNTIME_LIB_DIR="$(
  cd "$VOICE_GATEWAY_DIR"
  uv run python - <<'PY'
from pathlib import Path
import onnxruntime

print(Path(onnxruntime.__file__).resolve().parent / "capi")
PY
)"
ONNXRUNTIME_LIB="$ONNXRUNTIME_LIB_DIR/libonnxruntime.1.23.2.dylib"
SHERPA_ONNX_LIB_DIR="$(
  cd "$VOICE_GATEWAY_DIR"
  uv run python - <<'PY'
from importlib.util import find_spec
from pathlib import Path

spec = find_spec("sherpa_onnx")
if spec is None or spec.origin is None:
    raise SystemExit("sherpa_onnx package not found")
print(Path(spec.origin).resolve().parent / "lib")
PY
)"
SHERPA_ONNX_ONNXRUNTIME_LINK="$SHERPA_ONNX_LIB_DIR/libonnxruntime.1.23.2.dylib"
if [[ ! -f "$ONNXRUNTIME_LIB" ]]; then
  echo "ERROR: missing onnxruntime dylib at $ONNXRUNTIME_LIB" >&2
  exit 1
fi
if [[ ! -e "$SHERPA_ONNX_ONNXRUNTIME_LINK" ]]; then
  ln -s "$ONNXRUNTIME_LIB" "$SHERPA_ONNX_ONNXRUNTIME_LINK"
fi
export DYLD_LIBRARY_PATH="$ONNXRUNTIME_LIB_DIR${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"

if lsof -nP -iTCP:"$VOICE_GATEWAY_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "ERROR: port $VOICE_GATEWAY_PORT is already in use. Stop the existing voice-gateway server first." >&2
  lsof -nP -iTCP:"$VOICE_GATEWAY_PORT" -sTCP:LISTEN >&2 || true
  exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] starting voice-gateway minimal XiaoAI runtime"
echo "voice_gateway: $VOICE_GATEWAY_DIR"
echo "log: $LOG_FILE"
echo "events_log: $VOICE_GATEWAY_EVENTS_LOG_FILE"
echo "log_level: $VOICE_GATEWAY_LOG_LEVEL"
echo "event_level: $VOICE_GATEWAY_EVENT_LEVEL"
echo "audio_probe_level: $VOICE_GATEWAY_AUDIO_PROBE_LEVEL"
echo "suppress_audio_chunks: $VOICE_GATEWAY_SUPPRESS_AUDIO_CHUNKS"
echo "probe_interval_bytes: $VOICE_GATEWAY_PROBE_INTERVAL_BYTES"
echo "listen: $VOICE_GATEWAY_HOST:$VOICE_GATEWAY_PORT"
echo "metrics: http://$VOICE_GATEWAY_METRICS_HOST:$VOICE_GATEWAY_METRICS_PORT/metrics"
echo "otel_traces: $VOICE_GATEWAY_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
echo "wake_word: $VOICE_GATEWAY_WAKE_WORD"
echo "route: wake word -> random speaker text ack -> next utterance as Hermes question"
echo "openai_base_url: $VOICE_GATEWAY_OPENAI_BASE_URL"
echo "openai_model: $VOICE_GATEWAY_OPENAI_MODEL"
echo "tts_engine: edge"
echo "tts_voice: $VOICE_GATEWAY_TTS_VOICE"
echo "tts_rate: $VOICE_GATEWAY_TTS_RATE"
echo "tts_format: mp3"
echo "tts_http_base_url: $VOICE_GATEWAY_TTS_HTTP_BASE_URL"

cd "$VOICE_GATEWAY_DIR"
env PYTHONUNBUFFERED=1 uv run python -m voice_gateway.xiaoai_runtime \
  --wake-word "$VOICE_GATEWAY_WAKE_WORD" \
  --probe
