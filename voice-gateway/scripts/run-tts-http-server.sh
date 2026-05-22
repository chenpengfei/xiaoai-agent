#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICE_GATEWAY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TTS_DIR="${VOICE_GATEWAY_TTS_OUTPUT_DIR:-$VOICE_GATEWAY_DIR/audio-samples/tts}"
mkdir -p "$TTS_DIR"
cd "$TTS_DIR"
exec python3 -m http.server 8765 --bind 0.0.0.0
