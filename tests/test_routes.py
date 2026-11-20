"""HTTP route tests without loading a speech model or GPU."""

import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import api_router
from app.core.config import Settings
from app.core.limiter import limiter
from app.services.inference import InferenceService, TranscriptionResult
from tests.gateway_state import install_gateway_state

TEST_KEY = "unit-test-only-" + "x" * 32


@pytest.fixture
def application(monkeypatch, tmp_path):
    limiter.reset()
    settings = Settings(api_key=TEST_KEY, device="cpu", compute="int8", rate_limit="60/minute")
    result = TranscriptionResult(
        text="Olá mundo",
        segments=(),
        language="pt",
        language_probability=1.0,
        model=settings.model,
        device="cpu",
        audio_duration_seconds=1.0,
        processing_ms=10.0,
        queue_wait_ms=0.0,
        audio_offset_seconds=0.0,
    )
    inference = Mock(spec=InferenceService)
    inference.is_ready = True
    inference.transcribe = AsyncMock(return_value=result)
    inference.transcribe_window = AsyncMock(return_value=result)
    inference.prepare_audio = Mock(return_value=np.zeros(16000, dtype=np.float32))
    app = FastAPI()
    app.state.settings = settings
    install_gateway_state(app, tmp_path)
    monkeypatch.setattr("app.core.limiter.get_settings", lambda: app.state.settings)
    app.state.inference_service = inference
    app.state.stream_slots = asyncio.Semaphore(settings.stream_sessions or settings.sessions)
    app.state.rest_slots = asyncio.Semaphore(settings.rest_concurrency)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(api_router)
    yield app
    limiter.reset()


@pytest.mark.asyncio
async def test_health(application):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["ready"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}])
async def test_transcription_rejects_invalid_credentials(application, headers):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/audio/transcriptions",
            headers=headers,
            files={"file": ("test.wav", b"mock audio", "audio/wav")},
        )
    assert response.status_code == 401
    application.state.inference_service.transcribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcription_accepts_valid_credentials(application):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {TEST_KEY}"},
            files={"file": ("test.wav", b"mock audio", "audio/wav")},
        )
    assert response.status_code == 200
    assert response.json()["text"] == "Olá mundo"
    application.state.inference_service.transcribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_models_rate_limit(application):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        for _ in range(60):
            response = await client.get(
                "/v1/models", headers={"Authorization": f"Bearer {TEST_KEY}"}
            )
            assert response.status_code == 200
        response = await client.get("/v1/models", headers={"Authorization": f"Bearer {TEST_KEY}"})
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_rest_available_with_all_stream_slots_taken(application):
    slots = application.state.stream_slots
    for _ in range(application.state.settings.sessions):
        await slots.acquire()
    try:
        assert slots.locked()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {TEST_KEY}"},
                files={"file": ("test.wav", b"mock audio", "audio/wav")},
            )
        assert response.status_code == 200
    finally:
        for _ in range(application.state.settings.sessions):
            slots.release()


@pytest.mark.asyncio
async def test_legacy_rotation_shares_bucket_and_tokens_are_isolated(application):
    """Legacy credentials are metered as the internal app "legacy" (AUD-04).

    Rotating STT_API_KEY does not reset its limits, while each registered
    application keeps an independent bucket.
    """
    from pydantic import SecretStr

    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        settings = application.state.settings.model_copy(update={"rate_limit": "1/minute"})
        application.state.settings = settings
        headers = {"Authorization": f"Bearer {TEST_KEY}"}
        assert (await client.get("/v1/models", headers=headers)).status_code == 200
        assert (await client.get("/v1/models", headers=headers)).status_code == 429
        rotated = "another-key-" + "z" * 32
        application.state.settings = settings.model_copy(update={"api_key": SecretStr(rotated)})
        rotated_headers = {"Authorization": f"Bearer {rotated}"}
        assert (await client.get("/v1/models", headers=rotated_headers)).status_code == 429
        _, key = application.state.registry.register("Ritmo")
        token, _ = application.state.registry.issue_token(key)
        token_headers = {"Authorization": f"Bearer {token}"}
        assert (await client.get("/v1/models", headers=token_headers)).status_code == 200


def test_untrusted_proxy_header_ignored(application):
    from starlette.requests import Request
    from app.core.limiter import request_key

    request = Request(
        {
            "type": "http",
            "app": application,
            "client": ("127.0.0.1", 1234),
            "headers": [(b"cf-connecting-ip", b"203.0.113.5")],
        }
    )
    assert request_key(request) == "ip:127.0.0.1"
