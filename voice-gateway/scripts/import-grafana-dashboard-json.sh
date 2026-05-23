#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICE_GATEWAY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET_FILE="${DASHBOARD_JSON_FILE:-$VOICE_GATEWAY_DIR/ops/grafana/provisioning/dashboards/json/voice-gateway-overview.json}"
SOURCE_FILE="${1:-}"
TEMP_FILE=""

cleanup() {
  if [[ -n "$TEMP_FILE" && -f "$TEMP_FILE" ]]; then
    rm -f "$TEMP_FILE"
  fi
}
trap cleanup EXIT

if [[ -n "$SOURCE_FILE" ]]; then
  if [[ ! -f "$SOURCE_FILE" ]]; then
    echo "ERROR: source JSON file does not exist: $SOURCE_FILE" >&2
    exit 1
  fi
elif command -v pbpaste >/dev/null 2>&1; then
  TEMP_FILE="$(mktemp)"
  pbpaste >"$TEMP_FILE"
  SOURCE_FILE="$TEMP_FILE"
else
  TEMP_FILE="$(mktemp)"
  cat >"$TEMP_FILE"
  SOURCE_FILE="$TEMP_FILE"
fi

python3 - "$SOURCE_FILE" "$TARGET_FILE" <<'PY'
import json
import os
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])

with source_path.open(encoding="utf-8") as f:
    payload = json.load(f)

if isinstance(payload, str):
    payload = json.loads(payload)


def dashboard_candidates(value):
    if not isinstance(value, dict):
        return
    yield value
    for key in ("dashboard", "spec", "json"):
        nested = value.get(key)
        if isinstance(nested, str):
            try:
                nested = json.loads(nested)
            except json.JSONDecodeError:
                continue
        if isinstance(nested, dict):
            yield nested
            yield from dashboard_candidates(nested)


def find_dashboard(value):
    seen = set()
    for candidate in dashboard_candidates(value):
        ident = id(candidate)
        if ident in seen:
            continue
        seen.add(ident)
        if isinstance(candidate.get("panels"), list):
            return candidate
        if isinstance(candidate.get("elements"), dict) and isinstance(candidate.get("layout"), dict):
            return convert_grafana_v13_dashboard(candidate)
    return None


def convert_grafana_v13_dashboard(value):
    elements = value.get("elements") or {}
    layout_items = (((value.get("layout") or {}).get("spec") or {}).get("items") or [])
    grid_by_element = {}
    for item in layout_items:
        spec = item.get("spec") or {}
        element = spec.get("element") or {}
        name = element.get("name")
        if not name:
            continue
        grid_by_element[name] = {
            "h": spec.get("height", 8),
            "w": spec.get("width", 12),
            "x": spec.get("x", 0),
            "y": spec.get("y", 0),
        }

    panels = []
    for name, element in elements.items():
        if element.get("kind") != "Panel":
            continue
        spec = element.get("spec") or {}
        panel = {
            "id": spec.get("id"),
            "type": (((spec.get("vizConfig") or {}).get("group")) or "timeseries"),
            "title": spec.get("title", name),
            "datasource": _panel_datasource(spec),
            "targets": _panel_targets(spec),
            "gridPos": grid_by_element.get(name, {"h": 8, "w": 12, "x": 0, "y": 0}),
        }
        description = spec.get("description")
        if description:
            panel["description"] = description
        links = spec.get("links")
        if links:
            panel["links"] = links
        viz_spec = ((spec.get("vizConfig") or {}).get("spec") or {})
        if isinstance(viz_spec.get("fieldConfig"), dict):
            panel["fieldConfig"] = viz_spec["fieldConfig"]
        if isinstance(viz_spec.get("options"), dict):
            panel["options"] = viz_spec["options"]
        panels.append(panel)

    time_settings = value.get("timeSettings") or {}
    return {
        "uid": value.get("uid", "voice-gateway-overview"),
        "title": value.get("title", "Voice Gateway Overview"),
        "schemaVersion": value.get("schemaVersion", 39),
        "version": value.get("version", 1),
        "refresh": time_settings.get("autoRefresh", value.get("refresh", "10s")),
        "time": {
            "from": time_settings.get("from", "now-15m"),
            "to": time_settings.get("to", "now"),
        },
        "tags": value.get("tags", []),
        "panels": sorted(panels, key=lambda panel: (panel.get("gridPos", {}).get("y", 0), panel.get("gridPos", {}).get("x", 0), panel.get("id") or 0)),
    }


def _panel_datasource(spec):
    targets = _panel_targets(spec)
    if targets:
        datasource = targets[0].get("datasource")
        if isinstance(datasource, dict):
            return datasource
    return {"type": "prometheus", "uid": "prometheus"}


def _panel_targets(spec):
    query_group = (((spec.get("data") or {}).get("spec") or {}).get("queries") or [])
    targets = []
    for index, item in enumerate(query_group):
        panel_query = item.get("spec") or {}
        data_query = panel_query.get("query") or {}
        query_spec = dict(data_query.get("spec") or {})
        query_spec["refId"] = panel_query.get("refId") or chr(ord("A") + index)
        query_spec["datasource"] = _datasource_from_query(data_query)
        targets.append(query_spec)
    return targets


def _datasource_from_query(data_query):
    group = (data_query.get("group") or "").lower()
    name = ((data_query.get("datasource") or {}).get("name") or group).lower()
    if "loki" in {group, name}:
        return {"type": "loki", "uid": "loki"}
    if "tempo" in {group, name}:
        return {"type": "tempo", "uid": "tempo"}
    return {"type": "prometheus", "uid": "prometheus"}


dashboard = find_dashboard(payload)
if dashboard is None:
    if isinstance(payload, dict):
        keys = ", ".join(sorted(payload.keys()))
        if {"targets", "type"}.issubset(payload.keys()):
            hint = " It looks like a single panel JSON, not the whole dashboard JSON."
        else:
            hint = ""
        raise SystemExit(
            "ERROR: dashboard JSON does not contain panels. "
            f"Top-level keys: {keys or '<none>'}.{hint} "
            "In Grafana's Save dashboard dialog, click 'Copy JSON to clipboard' for the whole dashboard."
        )
    raise SystemExit("ERROR: JSON must be a Grafana dashboard object")

for key in ("id", "iteration"):
    dashboard.pop(key, None)

dashboard.setdefault("uid", "voice-gateway-overview")
dashboard.setdefault("title", "Voice Gateway Overview")
dashboard.setdefault("schemaVersion", 39)
dashboard.setdefault("version", 1)

if "panels" not in dashboard:
    raise SystemExit("ERROR: dashboard JSON does not contain panels")

target_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
with tmp_path.open("w", encoding="utf-8") as f:
    json.dump(dashboard, f, ensure_ascii=False, indent=2)
    f.write("\n")
os.replace(tmp_path, target_path)
PY

python3 -m json.tool "$TARGET_FILE" >/dev/null

echo "Updated: $TARGET_FILE"
echo "Grafana reloads provisioned dashboards automatically; wait up to 30 seconds or rerun ./scripts/run-ops-stack.sh."
