#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICE_GATEWAY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REMOTE_DIR="${VOICE_GATEWAY_REMOTE_DIR:-/data/voice-gateway}"
TARGET="${VOICE_GATEWAY_CLIENT_TARGET:-armv7-unknown-linux-gnueabihf}"
CLIENT_BIN="${VOICE_GATEWAY_CLIENT_BIN:-$VOICE_GATEWAY_DIR/device/client-rust/target/$TARGET/release/client}"
SPEAKER_USER="${VOICE_GATEWAY_SPEAKER_USER:-root}"
SPEAKER_HOST="${VOICE_GATEWAY_SPEAKER_HOST:-}"
SPEAKER_PASSWORD="${VOICE_GATEWAY_SPEAKER_PASSWORD:-}"

if [[ -z "$SPEAKER_HOST" ]]; then
  echo "ERROR: set VOICE_GATEWAY_SPEAKER_HOST, e.g. 192.168.1.23" >&2
  exit 1
fi

if [[ ! -f "$CLIENT_BIN" ]]; then
  echo "client binary not found: $CLIENT_BIN"
  echo "building with scripts/build-speaker-client.sh..."
  "$VOICE_GATEWAY_DIR/scripts/build-speaker-client.sh"
fi

if [[ ! -f "$CLIENT_BIN" ]]; then
  echo "ERROR: client binary still missing: $CLIENT_BIN" >&2
  exit 1
fi

SSH_OPTS=(-o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa)
REMOTE="$SPEAKER_USER@$SPEAKER_HOST"

run_ssh() {
  if [[ -n "$SPEAKER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
    SSHPASS="$SPEAKER_PASSWORD" sshpass -e ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"
  else
    ssh "${SSH_OPTS[@]}" "$REMOTE" "$@"
  fi
}

run_scp() {
  if [[ -n "$SPEAKER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
    SSHPASS="$SPEAKER_PASSWORD" sshpass -e scp "${SSH_OPTS[@]}" "$@"
  else
    scp "${SSH_OPTS[@]}" "$@"
  fi
}

run_ssh "mkdir -p '$REMOTE_DIR'"
run_scp "$CLIENT_BIN" "$REMOTE:$REMOTE_DIR/client.tmp"
run_ssh "mv '$REMOTE_DIR/client.tmp' '$REMOTE_DIR/client' && chmod +x '$REMOTE_DIR/client'"

"$VOICE_GATEWAY_DIR/scripts/configure-speaker-client.sh"

run_ssh "kill -9 \$(ps | grep 'voice-gateway/client' | grep -v grep | awk '{print \$1}') >/dev/null 2>&1 || true"
run_ssh "nohup '$REMOTE_DIR/client' \"\$(cat '$REMOTE_DIR/server.txt')\" >/tmp/voice-gateway-client.log 2>&1 &"

echo "installed speaker client:"
echo "  host: $SPEAKER_HOST"
echo "  binary: $CLIENT_BIN"
echo "  remote: $REMOTE_DIR/client"
