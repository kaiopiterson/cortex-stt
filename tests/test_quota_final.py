from fastapi.testclient import TestClient

from test_audit_regressions import build


def test_quota_exceeded_finalizes_admitted_audio(tmp_path):
    app, registry = build(tmp_path, app_daily_audio_seconds=1)
    _, key = registry.register("Ritmo")
    issued = registry.issue_token(key)
    assert issued is not None
    token, _ = issued
    with TestClient(app) as client:
        with client.websocket_connect("/v1/audio/stream") as ws:
            ws.send_json({"api_key": token, "mode": "utterance"})
            assert ws.receive_json()["type"] == "ready"
            ws.send_bytes(b"\x00\x00" * 16000)
            ws.send_bytes(b"\x00\x00" * 16000)
            final = ws.receive_json()
            assert final["type"] == "final"
            assert final["reason"] == "quota_exceeded"
            assert final["text"] == "olá"
            assert final["audio_duration_seconds"] == 1.0
