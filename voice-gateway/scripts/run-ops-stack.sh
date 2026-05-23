#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICE_GATEWAY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OPS_DIR="$VOICE_GATEWAY_DIR/ops"

mkdir -p \
  "$VOICE_GATEWAY_DIR/logs" \
  "$VOICE_GATEWAY_DIR/.ops-data/grafana" \
  "$VOICE_GATEWAY_DIR/.ops-data/loki" \
  "$VOICE_GATEWAY_DIR/.ops-data/tempo" \
  "$VOICE_GATEWAY_DIR/.ops-data/prometheus" \
  "$VOICE_GATEWAY_DIR/.ops-data/alloy"

touch \
  "$VOICE_GATEWAY_DIR/logs/events.jsonl" \
  "$VOICE_GATEWAY_DIR/logs/runtime.log" \
  "$VOICE_GATEWAY_DIR/logs/audit.jsonl" \
  "$VOICE_GATEWAY_DIR/logs/voice-gateway-minimal.log"

if [[ ! -f "$OPS_DIR/.env" ]]; then
  cp "$OPS_DIR/.env.example" "$OPS_DIR/.env"
fi

set -a
# shellcheck disable=SC1091
source "$OPS_DIR/.env"
set +a

GRAFANA_HTTP_PORT="${GRAFANA_HTTP_PORT:-3300}"
GRAFANA_ROOT_URL="${GRAFANA_ROOT_URL:-http://127.0.0.1:$GRAFANA_HTTP_PORT}"
export GRAFANA_HTTP_PORT GRAFANA_ROOT_URL

cd "$OPS_DIR"
if docker compose version >/dev/null 2>&1; then
  docker compose up -d
elif command -v docker-compose >/dev/null 2>&1; then
  docker-compose up -d
else
  echo "ERROR: docker compose or docker-compose is required." >&2
  exit 1
fi

echo "Grafana: http://127.0.0.1:$GRAFANA_HTTP_PORT"
echo "Loki:    http://127.0.0.1:3100"
echo "Tempo:   http://127.0.0.1:3200"
echo "Alloy:   http://127.0.0.1:12345"
echo "Prometheus: http://127.0.0.1:9090"
