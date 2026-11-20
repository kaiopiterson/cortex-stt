import asyncio
from unittest.mock import Mock

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.websocket import router
from app.core.config import Settings
from app.core.registry import AppRegistry
from app.core.usage import AppSlots, UsageStore


def test_revoked_session_and_shared_slots(tmp_path):
    app = FastAPI()
    app.state.settings = Settings(
        api_key="x" * 40, device="cpu", compute="int8", app_concurrency=1
    )
    registry = AppRegistry(str(tmp_path / "registry.db"))
    registry.initialize()
    identity, key = registry.register("Ritmo")
    token, _ = registry.issue_token(key)
    other, _ = registry.issue_token(key)
    app.state.registry = registry
    app.state.usage = UsageStore(registry)
    app.state.usage.initialize()
    app.state.app_slots = AppSlots()
    app.state.stream_slots = asyncio.Semaphore(3)
    app.state.inference_service = Mock(is_ready=True)
    app.include_router(router)
    with TestClient(app) as client:
        with client.websocket_connect("/v1/audio/stream") as ws:
            ws.send_json({"api_key": token, "mode": "utterance"})
            assert ws.receive_json()["type"] == "ready"
            with client.websocket_connect("/v1/audio/stream") as second:
                second.send_json({"api_key": other})
                assert second.receive_json()["code"] == "quota_exceeded"
            registry.revoke_tokens(identity)
            assert ws.receive_json()["code"] == "unauthorized"
    assert app.state.app_slots.counts == {}
