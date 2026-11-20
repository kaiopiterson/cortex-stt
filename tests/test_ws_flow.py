"""Protocol 1.1 integration tests with no native inference."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.websocket import router
from app.core.config import Settings
from app.services.inference import InferenceService, TranscriptionResult, TranscriptionSegment
from tests.gateway_state import install_gateway_state

KEY = "test-only-" + "x" * 40
PATH = "/v1/audio/stream"


@pytest.fixture
def application(tmp_path):
    settings = Settings(api_key=KEY, device="cpu", compute="int8", stream_auth_timeout_seconds=1)
    result = TranscriptionResult(
        "Olá",
        (TranscriptionSegment(0, 0.4, "Olá", -0.1, 0.1),),
        "pt",
        1,
        "small",
        "cpu",
        1,
        10,
        0,
        0,
    )
    service = Mock(spec=InferenceService)
    service.is_ready = True
    service.transcribe = AsyncMock(return_value=result)
    service.transcribe_window = AsyncMock(return_value=result)
    app = FastAPI()
    app.state.settings = settings
    app.state.inference_service = service
    app.state.stream_slots = asyncio.Semaphore(2)
    install_gateway_state(app, tmp_path)
    app.include_router(router)
    return app


@pytest.mark.parametrize("mode", ["stream", "utterance"])
def test_transcription_flow(application, mode):
    with TestClient(application) as client, client.websocket_connect(PATH) as ws:
        ws.send_json({"api_key": KEY, "mode": mode, "hotwords": ["Ritmo"]})
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["protocol_version"] == "1.1"
        ws.send_bytes(bytes(32000))
        if mode == "stream":
            assert ws.receive_json()["type"] == "partial"
        ws.send_text("finish")
        final = ws.receive_json()
        assert final["type"] == "final"
        assert final["text"] == "Olá"
        assert 0 <= final["confidence"] <= 1
        assert final["processing"]["location"] == "server"
    service = application.state.inference_service
    if mode == "utterance":
        service.transcribe.assert_awaited_once()
        service.transcribe_window.assert_not_awaited()
        assert service.transcribe.await_args.kwargs["hotwords"] == "Ritmo"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"api_key": "wrong"}, "unauthorized"),
        ({"sample_rate": 8000}, "unsupported_audio_format"),
        ({"language": "invalid"}, "invalid_language"),
        ({"mode": "invalid"}, "invalid_mode"),
        ({"hotwords": [1]}, "invalid_hotwords"),
    ],
)
def test_invalid_handshake(application, changes, code):
    with TestClient(application) as client, client.websocket_connect(PATH) as ws:
        ws.send_json({"api_key": KEY, **changes})
        assert ws.receive_json()["code"] == code
    application.state.inference_service.transcribe.assert_not_awaited()


def test_authentication_timeout(application):
    with TestClient(application) as client, client.websocket_connect(PATH) as ws:
        assert ws.receive_json()["code"] == "authentication_timeout"
