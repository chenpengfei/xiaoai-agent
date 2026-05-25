import json
import re
from urllib.request import urlopen

import pytest

from voice_gateway.observability import JsonLineEventLogger, runtime_log
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


def test_json_line_event_logger_filters_below_min_level(tmp_path):
    path = tmp_path / "events.jsonl"
    metrics = MetricsRegistry()
    logger = JsonLineEventLogger(event_log_file=path, min_level="warning", metrics_registry=metrics)

    logger.emit("hermes.completed", turn_id="t_1")
    logger.emit("wake_word.ignored", turn_id="t_1")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "wake_word.ignored"
    assert payload["level"] == "warning"
    rendered = metrics.render_prometheus()
    assert 'voice_gateway_events_total{event="hermes.completed",level="info"}' not in rendered
    assert 'voice_gateway_events_total{event="wake_word.ignored",level="warning"} 1' in rendered


def test_json_line_event_logger_uses_pretty_console_and_full_jsonl_file(tmp_path, capsys):
    path = tmp_path / "events.jsonl"
    logger = JsonLineEventLogger(
        event_log_file=path,
        metrics_registry=None,
        console_format="pretty",
        console_min_level="info",
    )

    logger.emit(
        "tts.started",
        conversation_id="c_abcdef123456",
        device_id="xiaoai-speaker",
        span_id="span_abcdef123456",
        trace_id="trace_abcdef123456",
        turn_id="t_123456789abc",
    )
    logger.emit("hermes.started", turn_id="t_123456789abc", user_text="一加二")
    logger.emit("hermes.completed", turn_id="t_123456789abc", response_text="一加二等于三。", latency_ms=1234)

    stderr = capsys.readouterr().err.splitlines()
    assert len(stderr) == 3
    assert re.match(r"\d\d:\d\d:\d\d\.\d{3} INFO  tts      started", stderr[0])
    assert "turn=t_123456" in stderr[0]
    assert "conv=c_abcdef" in stderr[0]
    assert "device=" not in stderr[0]
    assert "span" not in stderr[0]
    assert "trace" not in stderr[0]
    assert re.match(r"\d\d:\d\d:\d\d\.\d{3} INFO  llm      started", stderr[1])
    assert 'turn=t_123456 text="一加二"' in stderr[1]
    assert re.match(r"\d\d:\d\d:\d\d\.\d{3} INFO  llm      completed", stderr[2])
    assert 'turn=t_123456 cost=1234ms text="一加二等于三。"' in stderr[2]

    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event"] for line in lines] == [
        "tts.started",
        "hermes.started",
        "hermes.completed",
    ]
    assert json.loads(lines[0])["device_id"] == "xiaoai-speaker"
    assert json.loads(lines[0])["span_id"] == "span_abcdef123456"
    assert json.loads(lines[0])["trace_id"] == "trace_abcdef123456"
    assert json.loads(lines[0])["turn_id"] == "t_123456789abc"


def test_runtime_log_uses_human_readable_format(monkeypatch, capsys):
    monkeypatch.setenv("VOICE_GATEWAY_LOG_LEVEL", "INFO")

    runtime_log("gateway", "started", host="0.0.0.0", port=4399, protocol="xiaoai_ws")

    stderr = capsys.readouterr().err.strip()
    assert re.match(r"\d\d:\d\d:\d\d\.\d{3} INFO  gateway  started", stderr)
    assert "host=0.0.0.0" in stderr
    assert "port=4399" in stderr
    assert "protocol=xiaoai_ws" in stderr
    assert not stderr.startswith("{")


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


def test_metrics_registry_tracks_tts_latency():
    metrics = MetricsRegistry()

    metrics.observe_event(
        "tts.completed",
        {
            "level": "info",
            "latency_ms": 42,
        },
    )

    rendered = metrics.render_prometheus()
    assert "voice_gateway_tts_latency_ms_sum 42" in rendered
    assert "voice_gateway_tts_fallback_total" not in rendered
    assert "voice_gateway_tts_cache_hit_total" not in rendered


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
