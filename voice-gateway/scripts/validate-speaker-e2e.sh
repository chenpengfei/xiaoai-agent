#!/usr/bin/env bash
set -euo pipefail

REMOTE_DIR="${VOICE_GATEWAY_REMOTE_DIR:-/data/voice-gateway}"
SPEAKER_USER="${VOICE_GATEWAY_SPEAKER_USER:-root}"
SPEAKER_HOST="${VOICE_GATEWAY_SPEAKER_HOST:-}"
SPEAKER_PASSWORD="${VOICE_GATEWAY_SPEAKER_PASSWORD:-}"

if [[ -z "$SPEAKER_HOST" ]]; then
  echo "ERROR: set VOICE_GATEWAY_SPEAKER_HOST, e.g. 192.168.1.23" >&2
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

echo "checking SSH..."
run_ssh "echo ok"

echo "checking client files..."
run_ssh "test -x '$REMOTE_DIR/client'"
run_ssh "test -f '$REMOTE_DIR/server.txt' && cat '$REMOTE_DIR/server.txt'"

echo "checking client process..."
if run_ssh "ps | grep 'voice-gateway/client' | grep -v grep"; then
  echo "client process: running"
else
  echo "client process: not running" >&2
fi

echo "checking KWS files..."
if run_ssh "test -d '$REMOTE_DIR/kws'"; then
  run_ssh "ls '$REMOTE_DIR/kws' | sed -n '1,20p'"
else
  echo "KWS directory: not installed"
fi

echo "speaker validation completed"
