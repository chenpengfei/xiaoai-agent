from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional


TURN_SLOW_THRESHOLD_MS = 15_000
STAGE_SLOW_THRESHOLDS_MS = {
    "hermes": 10_000,
    "tts": 5_000,
    "playback": 10_000,
}


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.events_total: dict[tuple[str, str], int] = {}
        self.turn_total = 0
        self.turn_success_total = 0
        self.turn_failure_total = 0
        self.turn_slow_total = 0
        self.turn_duration_ms: list[int] = []
        self.stage_duration_ms: dict[str, list[int]] = {}
        self.stage_slow_total: dict[str, int] = {}
        self.slowest_stage_count: dict[str, int] = {}
        self.audio_bytes_total = 0
        self.audio_chunk_total = 0
        self.audio_last_seen = 0.0
        self.runtime_worker_failure_total = 0
        self.hermes_failure_total = 0
        self.tts_failure_total = 0
        self.tts_latency_ms: list[int] = []
        self.playback_failure_total = 0
        self.asr_empty_total = 0
        self.last_event_seen = 0.0

    def observe_event(self, event: str, fields: dict[str, Any]) -> None:
        now = time.time()
        level = str(fields.get("level") or "info")
        with self._lock:
            self.last_event_seen = now
            self.events_total[(event, level)] = self.events_total.get((event, level), 0) + 1
            if event == "audio.chunk.received":
                self.audio_chunk_total += 1
                self.audio_bytes_total += int(fields.get("bytes") or 0)
                self.audio_last_seen = now
            elif event == "runtime.worker.failed":
                self.runtime_worker_failure_total += 1
            elif event == "asr.completed" and not fields.get("normalized_text"):
                self.asr_empty_total += 1
            elif event == "hermes.failed":
                self.hermes_failure_total += 1
            elif event == "hermes.completed":
                self._observe_stage_latency("hermes", fields.get("latency_ms"))
            elif event == "tts.failed":
                self.tts_failure_total += 1
            elif event == "tts.completed":
                latency_ms = fields.get("latency_ms")
                if isinstance(latency_ms, int):
                    self.tts_latency_ms.append(latency_ms)
                self._observe_stage_latency("tts", latency_ms)
            elif event == "playback.failed":
                self.playback_failure_total += 1
            elif event == "playback.finished":
                self._observe_stage_latency("playback", fields.get("latency_ms"))
            elif event in {"turn.completed", "turn.failed"}:
                self.turn_total += 1
                total_ms = fields.get("total_ms")
                if isinstance(total_ms, int):
                    self.turn_duration_ms.append(total_ms)
                    if total_ms > TURN_SLOW_THRESHOLD_MS:
                        self.turn_slow_total += 1
                stage_ms = fields.get("stage_ms")
                if isinstance(stage_ms, dict):
                    for stage, value in stage_ms.items():
                        if isinstance(stage, str) and isinstance(value, int):
                            self.stage_duration_ms.setdefault(stage, []).append(value)
                slowest_stage = fields.get("slowest_stage")
                if isinstance(slowest_stage, str):
                    self.slowest_stage_count[slowest_stage] = self.slowest_stage_count.get(slowest_stage, 0) + 1
                if event == "turn.completed":
                    self.turn_success_total += 1
                else:
                    self.turn_failure_total += 1

    def _observe_stage_latency(self, stage: str, latency_ms: Any) -> None:
        threshold_ms = STAGE_SLOW_THRESHOLDS_MS.get(stage)
        if threshold_ms is None or not isinstance(latency_ms, int):
            return
        if latency_ms > threshold_ms:
            self.stage_slow_total[stage] = self.stage_slow_total.get(stage, 0) + 1

    def render_prometheus(self) -> str:
        with self._lock:
            lines = [
                "# HELP voice_gateway_up Whether the voice gateway process is up.",
                "# TYPE voice_gateway_up gauge",
                "voice_gateway_up 1",
                "# HELP voice_gateway_uptime_seconds Process uptime in seconds.",
                "# TYPE voice_gateway_uptime_seconds gauge",
                f"voice_gateway_uptime_seconds {time.time() - self.started_at:.3f}",
                "# HELP voice_gateway_event_log_last_write_age_seconds Seconds since the last observed event.",
                "# TYPE voice_gateway_event_log_last_write_age_seconds gauge",
                f"voice_gateway_event_log_last_write_age_seconds {_age(self.last_event_seen):.3f}",
                "# HELP voice_gateway_audio_last_seen_age_seconds Seconds since the last audio chunk event.",
                "# TYPE voice_gateway_audio_last_seen_age_seconds gauge",
                f"voice_gateway_audio_last_seen_age_seconds {_age(self.audio_last_seen):.3f}",
                "# HELP voice_gateway_audio_chunk_total Total audio chunks observed.",
                "# TYPE voice_gateway_audio_chunk_total counter",
                f"voice_gateway_audio_chunk_total {self.audio_chunk_total}",
                "# HELP voice_gateway_audio_bytes_total Total audio bytes observed.",
                "# TYPE voice_gateway_audio_bytes_total counter",
                f"voice_gateway_audio_bytes_total {self.audio_bytes_total}",
                "# HELP voice_gateway_turn_total Total turns.",
                "# TYPE voice_gateway_turn_total counter",
                f"voice_gateway_turn_total {self.turn_total}",
                "# HELP voice_gateway_turn_success_total Successful turns.",
                "# TYPE voice_gateway_turn_success_total counter",
                f"voice_gateway_turn_success_total {self.turn_success_total}",
                "# HELP voice_gateway_turn_failure_total Failed turns.",
                "# TYPE voice_gateway_turn_failure_total counter",
                f"voice_gateway_turn_failure_total {self.turn_failure_total}",
                "# HELP voice_gateway_turn_slow_total Single turns slower than the alert threshold.",
                "# TYPE voice_gateway_turn_slow_total counter",
                f"voice_gateway_turn_slow_total {self.turn_slow_total}",
                "# HELP voice_gateway_runtime_worker_failure_total Runtime worker failures.",
                "# TYPE voice_gateway_runtime_worker_failure_total counter",
                f"voice_gateway_runtime_worker_failure_total {self.runtime_worker_failure_total}",
                "# HELP voice_gateway_asr_empty_total Empty ASR results.",
                "# TYPE voice_gateway_asr_empty_total counter",
                f"voice_gateway_asr_empty_total {self.asr_empty_total}",
                "# HELP voice_gateway_hermes_failure_total Hermes failures.",
                "# TYPE voice_gateway_hermes_failure_total counter",
                f"voice_gateway_hermes_failure_total {self.hermes_failure_total}",
                "# HELP voice_gateway_tts_failure_total TTS failures.",
                "# TYPE voice_gateway_tts_failure_total counter",
                f"voice_gateway_tts_failure_total {self.tts_failure_total}",
                "# HELP voice_gateway_playback_failure_total Playback failures.",
                "# TYPE voice_gateway_playback_failure_total counter",
                f"voice_gateway_playback_failure_total {self.playback_failure_total}",
            ]
            lines.extend(_summary("voice_gateway_turn_duration_ms", self.turn_duration_ms))
            lines.extend(_summary("voice_gateway_tts_latency_ms", self.tts_latency_ms))
            for stage, values in sorted(self.stage_duration_ms.items()):
                lines.extend(_summary("voice_gateway_turn_stage_duration_ms", values, {"stage": stage}))
            lines.append("# HELP voice_gateway_turn_slowest_stage_count Slowest stage counts.")
            lines.append("# TYPE voice_gateway_turn_slowest_stage_count counter")
            for stage, count in sorted(self.slowest_stage_count.items()):
                lines.append(f'voice_gateway_turn_slowest_stage_count{{stage="{_escape(stage)}"}} {count}')
            lines.append("# HELP voice_gateway_stage_slow_total Single stages slower than the alert threshold.")
            lines.append("# TYPE voice_gateway_stage_slow_total counter")
            for stage in sorted(STAGE_SLOW_THRESHOLDS_MS):
                count = self.stage_slow_total.get(stage, 0)
                lines.append(f'voice_gateway_stage_slow_total{{stage="{_escape(stage)}"}} {count}')
            lines.append("# HELP voice_gateway_events_total Structured events by event and level.")
            lines.append("# TYPE voice_gateway_events_total counter")
            for (event, level), count in sorted(self.events_total.items()):
                lines.append(
                    f'voice_gateway_events_total{{event="{_escape(event)}",level="{_escape(level)}"}} {count}'
                )
            return "\n".join(lines) + "\n"


DEFAULT_METRICS_REGISTRY = MetricsRegistry()


def start_metrics_server(
    *,
    host: str = "127.0.0.1",
    port: int = 9109,
    registry: MetricsRegistry = DEFAULT_METRICS_REGISTRY,
) -> ThreadingHTTPServer:
    class MetricsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path not in {"/metrics", "/health"}:
                self.send_response(404)
                self.end_headers()
                return
            body = b"ok\n" if self.path == "/health" else registry.render_prometheus().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, name="voice-gateway-metrics", daemon=True)
    thread.start()
    return server


def _age(timestamp: float) -> float:
    if timestamp <= 0:
        return 1_000_000_000.0
    return max(0.0, time.time() - timestamp)


def _summary(name: str, values: list[int], labels: Optional[dict[str, str]] = None) -> list[str]:
    label_text = _labels(labels or {})
    if not values:
        return [
            f"# HELP {name} Observed duration in milliseconds.",
            f"# TYPE {name} summary",
            f"{name}_count{label_text} 0",
            f"{name}_sum{label_text} 0",
        ]
    ordered = sorted(values)
    return [
        f"# HELP {name} Observed duration in milliseconds.",
        f"# TYPE {name} summary",
        f"{name}_count{label_text} {len(values)}",
        f"{name}_sum{label_text} {sum(values)}",
        f'{name}{{quantile="0.5"{_label_suffix(labels)}}} {_quantile(ordered, 0.5)}',
        f'{name}{{quantile="0.95"{_label_suffix(labels)}}} {_quantile(ordered, 0.95)}',
    ]


def _quantile(values: list[int], quantile: float) -> int:
    index = min(len(values) - 1, max(0, round((len(values) - 1) * quantile)))
    return values[index]


def _labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in sorted(labels.items())) + "}"


def _label_suffix(labels: Optional[dict[str, str]]) -> str:
    if not labels:
        return ""
    return "," + ",".join(f'{key}="{_escape(value)}"' for key, value in sorted(labels.items()))


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
