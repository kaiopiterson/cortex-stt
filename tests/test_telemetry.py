"""AUD-15: Prometheus exposition, bounded labels, request id and JSON logs."""

import json
import logging

from fastapi.testclient import TestClient

from app.core import telemetry
from app.core.observability import JsonFormatter, RequestContext, RequestIdFilter
from test_audit_regressions import build

METRICS = "M" * 40


def scrape(client):
    response = client.get("/metrics", headers={"Authorization": f"Bearer {METRICS}"})
    assert response.status_code == 200
    return response.text


def instrumented(tmp_path, **settings):
    app, registry = build(tmp_path, metrics_token=METRICS, **settings)
    app.state.inference_service.metrics_snapshot = lambda: {"stt_inference_active": 0}
    app.add_middleware(RequestContext)
    return app, registry


def test_exposition_contains_registry_and_gauges():
    telemetry.inference_duration.observe(0.2)
    telemetry.inference_success.inc()
    output = telemetry.render({"stt_inference_active": 3})
    assert "stt_inference_duration_seconds_bucket" in output
    assert "stt_inference_success_total" in output
    assert "# TYPE stt_inference_active gauge" in output
    assert "stt_inference_active 3" in output
    assert "_created" not in output


def test_http_metrics_use_route_templates_not_raw_paths(tmp_path):
    app, registry = instrumented(tmp_path)
    app_id, key = registry.register("Ritmo")
    token, _ = registry.issue_token(key, subject="alice")
    with TestClient(app) as client:
        client.post(
            "/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("a.wav", b"RIFF....", "audio/wav")},
        )
        client.get("/v1/does-not-exist/" + "x" * 30)
        client.post("/v1/audio/transcriptions", files={"file": ("a.wav", b"x", "audio/wav")})
        output = scrape(client)
    assert 'route="/v1/audio/transcriptions"' in output
    assert 'route="unmatched"' in output
    assert "x" * 30 not in output
    for secret_or_id in (app_id, token, key, "alice"):
        assert secret_or_id not in output
    assert 'stt_auth_failures_total{surface="rest"}' in output
    assert 'stt_rejections_total{reason="unauthorized"}' in output


def test_request_id_is_propagated_or_generated(tmp_path):
    app, _ = instrumented(tmp_path)
    with TestClient(app) as client:
        echoed = client.get("/health", headers={"X-Request-ID": "trace-123"})
        generated = client.get("/health", headers={"X-Request-ID": "bad id with spaces"})
    assert echoed.headers["x-request-id"] == "trace-123"
    assert generated.headers["x-request-id"] != "bad id with spaces"
    assert len(generated.headers["x-request-id"]) == 32


def test_json_access_log_has_no_credentials(tmp_path):
    app, registry = instrumented(tmp_path)
    _, key = registry.register("Ritmo")
    token, _ = registry.issue_token(key)
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    handler = Capture()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())
    access = logging.getLogger("app.access")
    access.addHandler(handler)
    access.setLevel(logging.INFO)
    try:
        with TestClient(app) as client:
            client.post(
                "/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {token}", "X-Request-ID": "req-1"},
                files={"file": ("a.wav", b"RIFF....", "audio/wav")},
            )
    finally:
        access.removeHandler(handler)
    entries = [json.loads(line) for line in records]
    entry = next(e for e in entries if e.get("route") == "/v1/audio/transcriptions")
    assert entry["request_id"] == "req-1"
    assert entry["status"] == 200
    assert {"ts", "level", "method", "duration_ms"} <= entry.keys()
    assert token not in "\n".join(records) and key not in "\n".join(records)


def test_stream_metrics_record_sessions_and_final_latency(tmp_path):
    app, registry = instrumented(tmp_path)
    _, key = registry.register("Ritmo")
    token, _ = registry.issue_token(key)
    with TestClient(app) as client:
        with client.websocket_connect("/v1/audio/stream") as ws:
            ws.send_json({"api_key": token, "mode": "utterance"})
            assert ws.receive_json()["type"] == "ready"
            ws.send_bytes(b"\x00\x00" * 8000)
            ws.send_text("finish")
            assert ws.receive_json()["type"] == "final"
        output = scrape(client)
    assert 'stt_stream_sessions_total{mode="utterance"}' in output
    assert 'stt_stream_final_latency_seconds_count{mode="utterance"}' in output
