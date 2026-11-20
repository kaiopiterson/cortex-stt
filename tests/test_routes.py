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
    app.state.session_slots = asyncio.Semaphore(settings.sessions)
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
