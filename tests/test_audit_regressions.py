"""Regressões da auditoria v0.3.0 (AUD-01, 02, 04, 05, 06).

Estes testes descrevem o comportamento CORRETO esperado. No commit dd23a3b
todos falham; devem passar após as correções. Não remova nem enfraqueça.
"""
import asyncio
import time
from unittest.mock import AsyncMock, Mock

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import api_router
from app.core.body_limit import BodyLimit
from app.core.config import Settings
from app.core.limiter import limiter
from app.core.registry import AppRegistry
from app.core.usage import AppSlots, UsageStore
from app.services.inference import (
    InferenceService,
    TranscriptionResult,
    TranscriptionSegment,
)

LEGACY = "L" * 40


def build(tmp_path, **overrides):
    app = FastAPI()
    settings = Settings(api_key=LEGACY, device="cpu", compute="int8", **overrides)
    app.state.settings = settings
    registry = AppRegistry(str(tmp_path / "registry.db"))
    registry.initialize()
    app.state.registry = registry
    app.state.usage = UsageStore(registry)
    app.state.usage.initialize()
    app.state.app_slots = AppSlots()
    app.state.stream_slots = asyncio.Semaphore(10)
    app.state.rest_slots = asyncio.Semaphore(10)
    app.state.limiter = limiter
    segment = TranscriptionSegment(0.0, 0.5, " olá", -0.1, 0.01)
    result = TranscriptionResult(
        text="olá", segments=(segment,), language="pt", language_probability=1.0,
        model="small", device="cpu", audio_duration_seconds=0.5, processing_ms=5,
        queue_wait_ms=0, audio_offset_seconds=0,
    )
    inference = Mock(spec=InferenceService)
    inference.is_ready = True
    inference.transcribe = AsyncMock(return_value=result)
    inference.transcribe_window = AsyncMock(return_value=result)
    inference._prepare_audio = Mock(return_value=np.zeros(8000, dtype=np.float32))
    if hasattr(InferenceService, "prepare_audio"):
        inference.prepare_audio = Mock(return_value=np.zeros(8000, dtype=np.float32))
    app.state.inference_service = inference
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(BodyLimit)
    app.include_router(api_router)
    limiter.reset()
    return app, registry


def upload(client, credential):
    return client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {credential}"},
        files={"file": ("a.wav", b"RIFF....", "audio/wav")},
    )


def test_aud01_token_endpoint_issues_tokens(tmp_path):
    app, registry = build(tmp_path)
    _, key = registry.register("Ritmo")
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/v1/auth/token", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    assert response.json()["access_token"].startswith("tok_")


def test_aud02_admin_body_not_parsed_without_auth(tmp_path):
    app, _ = build(tmp_path)  # STT_ADMIN_KEY ausente: admin desabilitado
    with TestClient(app) as client:
        response = client.post(
            "/v1/admin/apps", content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 404


def test_aud02_admin_body_not_parsed_with_wrong_key(tmp_path):
    app, _ = build(tmp_path, admin_key="A" * 40)
    with TestClient(app) as client:
        response = client.post(
            "/v1/admin/apps", content=b"{not json",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + "B" * 40},
        )
    assert response.status_code == 401


def test_aud02_small_body_limit_outside_upload(tmp_path):
    app, _ = build(tmp_path, admin_key="A" * 40)
    with TestClient(app) as client:
        response = client.post(
            "/v1/admin/apps", content=b"x" * (2 * 1024 * 1024),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + "A" * 40},
        )
    assert response.status_code == 413


def test_aud05_long_lived_key_cannot_transcribe(tmp_path):
    app, registry = build(tmp_path)
    _, key = registry.register("Ritmo")
    with TestClient(app) as client:
        response = upload(client, key)
    assert response.status_code == 403


def test_aud05_token_can_transcribe(tmp_path):
    app, registry = build(tmp_path)
    _, key = registry.register("Ritmo")
    token, _ = registry.issue_token(key)
    with TestClient(app) as client:
        response = upload(client, token)
    assert response.status_code == 200


def test_aud04_legacy_key_disabled_in_production(tmp_path):
    app, _ = build(tmp_path, environment="production")
    with TestClient(app) as client:
        response = upload(client, LEGACY)
    assert response.status_code == 401


def test_aud04_legacy_key_is_metered_when_enabled(tmp_path):
    app, _ = build(tmp_path, legacy_api_key_enabled=True, app_requests_per_minute=1)
    with TestClient(app) as client:
        codes = [upload(client, LEGACY).status_code for _ in range(3)]
    assert codes[0] == 200
    assert 429 in codes[1:]


def test_aud06_token_expiry_keeps_session_and_final(tmp_path):
    app, registry = build(tmp_path)
    _, key = registry.register("Ritmo")
    token, _ = registry.issue_token(key, ttl=1)
    with TestClient(app) as client:
        with client.websocket_connect("/v1/audio/stream") as ws:
            ws.send_json({"api_key": token, "mode": "utterance"})
            assert ws.receive_json()["type"] == "ready"
            ws.send_bytes(b"\x00\x00" * 16000)
            time.sleep(2.2)
            ws.send_text("finish")
            message = ws.receive_json()
    assert message["type"] == "final"


def test_aud06_revocation_still_closes_session(tmp_path):
    app, registry = build(tmp_path)
    app_id, key = registry.register("Ritmo")
    token, _ = registry.issue_token(key)
    with TestClient(app) as client:
        with client.websocket_connect("/v1/audio/stream") as ws:
            ws.send_json({"api_key": token, "mode": "utterance"})
            assert ws.receive_json()["type"] == "ready"
            registry.revoke_tokens(app_id)
            message = ws.receive_json()
    assert message["type"] in {"error", "final"}
    if message["type"] == "error":
        assert message["code"] in {"unauthorized", "revoked"}
