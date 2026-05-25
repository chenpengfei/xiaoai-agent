#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICE_GATEWAY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REMOTE_DIR="${VOICE_GATEWAY_REMOTE_DIR:-/data/voice-gateway}"
REMOTE_KWS_DIR="$REMOTE_DIR/kws"
SPEAKER_USER="${VOICE_GATEWAY_SPEAKER_USER:-root}"
SPEAKER_HOST="${VOICE_GATEWAY_SPEAKER_HOST:-}"
SPEAKER_PASSWORD="${VOICE_GATEWAY_SPEAKER_PASSWORD:-}"
KWS_ARTIFACT_DIR="${VOICE_GATEWAY_KWS_ARTIFACT_DIR:-}"
INSTALL_BOOT="${VOICE_GATEWAY_INSTALL_KWS_BOOT:-0}"

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

run_scp() {
  if [[ -n "$SPEAKER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
    SSHPASS="$SPEAKER_PASSWORD" sshpass -e scp "${SSH_OPTS[@]}" "$@"
  else
    scp "${SSH_OPTS[@]}" "$@"
  fi
}

run_ssh "mkdir -p '$REMOTE_KWS_DIR'"
run_scp "$VOICE_GATEWAY_DIR/client/kws/init.sh" "$REMOTE:$REMOTE_KWS_DIR/init.sh"
run_scp "$VOICE_GATEWAY_DIR/client/kws/boot.sh" "$REMOTE:$REMOTE_KWS_DIR/boot.sh"
run_scp "$VOICE_GATEWAY_DIR/client/kws/debug.sh" "$REMOTE:$REMOTE_KWS_DIR/debug.sh"
run_scp "$VOICE_GATEWAY_DIR/client/kws/keywords.txt" "$REMOTE:$REMOTE_KWS_DIR/keywords.txt"

if [[ -n "$KWS_ARTIFACT_DIR" ]]; then
  if [[ ! -d "$KWS_ARTIFACT_DIR" ]]; then
    echo "ERROR: VOICE_GATEWAY_KWS_ARTIFACT_DIR does not exist: $KWS_ARTIFACT_DIR" >&2
    exit 1
  fi
  run_scp -r "$KWS_ARTIFACT_DIR/"* "$REMOTE:$REMOTE_KWS_DIR/"
fi

run_ssh "chmod +x '$REMOTE_KWS_DIR/init.sh' '$REMOTE_KWS_DIR/boot.sh' '$REMOTE_KWS_DIR/debug.sh' >/dev/null 2>&1 || true"

if [[ "$INSTALL_BOOT" == "1" ]]; then
  run_ssh "cp '$REMOTE_KWS_DIR/boot.sh' /data/init.sh && chmod +x /data/init.sh"
fi

echo "installed speaker KWS files:"
echo "  host: $SPEAKER_HOST"
echo "  remote: $REMOTE_KWS_DIR"
echo "  artifact_dir: ${KWS_ARTIFACT_DIR:-not uploaded}"
