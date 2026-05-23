# Voice Gateway Ops Stack

This directory contains the local Grafana observability stack for `voice-gateway`.

```text
voice-gateway logs
  -> Alloy
  -> Loki
  -> Grafana Explore

voice-gateway OTLP traces
  -> Alloy
  -> Tempo
  -> Grafana Trace View

voice-gateway /metrics
  -> Alloy
  -> Prometheus
  -> Grafana Dashboard / Alerting
```

## Start

```sh
cd /Users/chenpengfei/projects/vibe-coding/xiaoai-agent/voice-gateway
cp ops/.env.example ops/.env
# Fill DISCORD_WEBHOOK_URL in ops/.env if alert delivery should be enabled.
./scripts/run-ops-stack.sh
```

OpenTelemetry export is enabled by `scripts/run-voice-gateway-minimal.sh` through `VOICE_GATEWAY_OTEL_ENABLED=1`. The runtime dependencies are part of this package; if tracing is disabled explicitly, `voice-gateway` still emits `trace_id` / `span_id` in logs and keeps the Loki slow-link fallback working.

```sh
VOICE_GATEWAY_OTEL_ENABLED=0 ./scripts/run-voice-gateway-minimal.sh
```

Grafana listens on:

```text
http://127.0.0.1:3300
```

Grafana alert links also use `GRAFANA_ROOT_URL` from `ops/.env`; keep it aligned with `GRAFANA_HTTP_PORT`.

## Persist Dashboard Edits

Provisioned dashboards cannot be saved directly from the Grafana UI. To persist a UI edit:

1. Click `Save dashboard`.
2. Click `Copy JSON to clipboard` in the save dialog.
3. Run:

```sh
./scripts/import-grafana-dashboard-json.sh
```

The script reads the dashboard JSON from the macOS clipboard, normalizes it, and writes it back to:

```text
voice-gateway/ops/grafana/provisioning/dashboards/json/voice-gateway-overview.json
```

Grafana reloads provisioned dashboards automatically within `30s`; rerun `./scripts/run-ops-stack.sh` to force a restart.

Prometheus listens on:

```text
http://127.0.0.1:9090
```

Default local login:

```text
admin / admin
```

## Data

Runtime data is stored under:

```text
voice-gateway/.ops-data/
```

That directory is intentionally ignored by git.
