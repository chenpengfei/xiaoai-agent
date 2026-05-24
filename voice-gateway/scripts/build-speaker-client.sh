#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICE_GATEWAY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CLIENT_DIR="$VOICE_GATEWAY_DIR/device/client-rust"

TARGET="${VOICE_GATEWAY_CLIENT_TARGET:-armv7-unknown-linux-gnueabihf}"
BUILD_TOOL="${VOICE_GATEWAY_CLIENT_BUILD_TOOL:-cross}"

if [[ ! -d "$CLIENT_DIR" ]]; then
  echo "ERROR: missing speaker client source at $CLIENT_DIR" >&2
  exit 1
fi

cd "$CLIENT_DIR"

if [[ "$TARGET" == "host" ]]; then
  cargo build --release --bin client
  cargo build --release --bin monitor
  echo "client: $CLIENT_DIR/target/release/client"
  echo "monitor: $CLIENT_DIR/target/release/monitor"
  exit 0
fi

if ! command -v "$BUILD_TOOL" >/dev/null 2>&1; then
  echo "ERROR: $BUILD_TOOL not found. Install cross or set VOICE_GATEWAY_CLIENT_BUILD_TOOL=cargo." >&2
  exit 1
fi

"$BUILD_TOOL" build --release --target "$TARGET" --bin client
"$BUILD_TOOL" build --release --target "$TARGET" --bin monitor

echo "client: $CLIENT_DIR/target/$TARGET/release/client"
echo "monitor: $CLIENT_DIR/target/$TARGET/release/monitor"
