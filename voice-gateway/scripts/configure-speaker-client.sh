#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICE_GATEWAY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REMOTE_DIR="${VOICE_GATEWAY_REMOTE_DIR:-/data/voice-gateway}"
SPEAKER_USER="${VOICE_GATEWAY_SPEAKER_USER:-root}"
SPEAKER_HOST="${VOICE_GATEWAY_SPEAKER_HOST:-}"
SERVER_URL="${VOICE_GATEWAY_SERVER_URL:-}"
SPEAKER_PASSWORD="${VOICE_GATEWAY_SPEAKER_PASSWORD:-}"

if [[ -z "$SPEAKER_HOST" ]]; then
  echo "ERROR: set VOICE_GATEWAY_SPEAKER_HOST, e.g. 192.168.1.23" >&2
  exit 1
fi

if [[ -z "$SERVER_URL" ]]; then
  echo "ERROR: set VOICE_GATEWAY_SERVER_URL, e.g. ws://192.168.1.9:4399" >&2
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
run_scp "$VOICE_GATEWAY_DIR/client/client-rust/init.sh" "$REMOTE:$REMOTE_DIR/init.sh"
run_scp "$VOICE_GATEWAY_DIR/client/client-rust/boot.sh" "$REMOTE:$REMOTE_DIR/boot.sh"
run_ssh "printf '%s\n' '$SERVER_URL' > '$REMOTE_DIR/server.txt' && chmod +x '$REMOTE_DIR/init.sh' '$REMOTE_DIR/boot.sh' && cp '$REMOTE_DIR/boot.sh' /data/init.sh && chmod +x /data/init.sh"

echo "configured speaker client:"
echo "  host: $SPEAKER_HOST"
echo "  server: $SERVER_URL"
echo "  remote_dir: $REMOTE_DIR"
