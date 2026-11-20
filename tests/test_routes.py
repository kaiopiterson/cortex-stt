"""HTTP route tests without loading a speech model or GPU."""
import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from app.api import api_router
from app.core.config import Settings
from app.core.limiter import limiter
from app.services.inference import InferenceService, TranscriptionResult

TEST_KEY = "unit-test-only-" + "x" * 32


@pytest.fixture
def application():
    limiter.reset()
    settings = Settings(api_key=TEST_KEY, device="cpu", compute="int8")
    result = TranscriptionResult(
        text="Olá mundo", segments=(), language="pt", language_probability=1.0,
        model=settings.model, device="cpu", audio_duration_seconds=1.0,
        processing_ms=10.0, queue_wait_ms=0.0, audio_offset_seconds=0.0,
    )
    inference = Mock(spec=InferenceService)
    inference.is_ready = True
    inference.transcribe = AsyncMock(return_value=result)
    inference.transcribe_window = AsyncMock(return_value=result)
    app = FastAPI()
    app.state.settings = settings
    app.state.inference_service = inference
    app.state.stream_slots = asyncio.Semaphore(settings.stream_sessions or settings.sessions)
    app.state.rest_slots = asyncio.Semaphore(settings.rest_concurrency)
    app.state.limiter = limiter
    app.include_router(api_router)
    yield app
    limiter.reset()


@pytest.mark.asyncio
async def test_health(application):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["ready"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}])
async def test_transcription_rejects_invalid_credentials(application, headers):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.post("/v1/audio/transcriptions", headers=headers,
                                     files={"file": ("test.wav", b"mock audio", "audio/wav")})
    assert response.status_code == 401
    application.state.inference_service.transcribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_transcription_accepts_valid_credentials(application):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.post("/v1/audio/transcriptions",
                                     headers={"Authorization": f"Bearer {TEST_KEY}"},
                                     files={"file": ("test.wav", b"mock audio", "audio/wav")})
    assert response.status_code == 200
    assert response.json()["text"] == "Olá mundo"
    application.state.inference_service.transcribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_models_rate_limit(application):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        for _ in range(60):
            response = await client.get("/v1/models", headers={"Authorization": f"Bearer {TEST_KEY}"})
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
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
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
async def test_distinct_keys_have_distinct_buckets(application):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        application.state.settings = application.state.settings.model_copy(update={"rate_limit": "1/minute"})
        headers = {"Authorization": f"Bearer {TEST_KEY}"}
        assert (await client.get("/v1/models", headers=headers)).status_code == 200
        assert (await client.get("/v1/models", headers=headers)).status_code == 429
        from pydantic import SecretStr
        other = "another-key-" + "z" * 32
        application.state.settings = application.state.settings.model_copy(update={"api_key": SecretStr(other)})
        assert (await client.get("/v1/models", headers={"Authorization": f"Bearer {other}"})).status_code == 200


def test_untrusted_proxy_header_ignored(application):
    from starlette.requests import Request
    from app.core.limiter import request_key
    request = Request({"type": "http", "app": application, "client": ("127.0.0.1", 1234),
                       "headers": [(b"cf-connecting-ip", b"203.0.113.5")]})
    assert request_key(request) == "ip:127.0.0.1"
