import json
from urllib.request import urlopen

import pytest

from voice_gateway.observability import JsonLineEventLogger
from voice_gateway.observability.metrics import MetricsRegistry, start_metrics_server


def test_json_line_event_logger_writes_events_file(tmp_path):
    path = tmp_path / "events.jsonl"
    metrics = MetricsRegistry()
    logger = JsonLineEventLogger(event_log_file=path, metrics_registry=metrics)

    logger.emit("hermes.completed", turn_id="t_1", latency_ms=123)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["event"] == "hermes.completed"
    assert payload["service"] == "voice-gateway"
    assert payload["level"] == "info"
    assert payload["turn_id"] == "t_1"
    assert payload["latency_ms"] == 123
    assert 'voice_gateway_events_total{event="hermes.completed",level="info"} 1' in metrics.render_prometheus()


def test_metrics_registry_tracks_turn_summary():
    metrics = MetricsRegistry()

    metrics.observe_event(
        "turn.completed",
        {
            "level": "info",
            "total_ms": 1234,
            "stage_ms": {"asr": 100, "hermes": 900},
            "slowest_stage": "hermes",
        },
    )

    rendered = metrics.render_prometheus()
    assert "voice_gateway_turn_success_total 1" in rendered
    assert "voice_gateway_turn_duration_ms_sum 1234" in rendered
    assert 'voice_gateway_turn_stage_duration_ms_sum{stage="hermes"} 900' in rendered
    assert 'voice_gateway_turn_slowest_stage_count{stage="hermes"} 1' in rendered


def test_metrics_registry_uses_finite_age_before_first_event():
    metrics = MetricsRegistry()

    rendered = metrics.render_prometheus()

    assert "inf" not in rendered
    assert "voice_gateway_event_log_last_write_age_seconds 1000000000.000" in rendered


def test_metrics_server_exposes_prometheus_endpoint():
    metrics = MetricsRegistry()
    metrics.observe_event("turn.completed", {"level": "info", "total_ms": 10})
    try:
        server = start_metrics_server(port=0, registry=metrics)
    except PermissionError:
        pytest.skip("local socket binding is not permitted in this environment")
    try:
        host, port = server.server_address
        with urlopen(f"http://{host}:{port}/metrics", timeout=2) as response:
            body = response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()

    assert response.status == 200
    assert "voice_gateway_up 1" in body
    assert "voice_gateway_turn_success_total 1" in body
