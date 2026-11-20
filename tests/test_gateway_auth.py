from types import SimpleNamespace

import pytest
from starlette.requests import HTTPConnection

from app.core.auth import identify_client
from app.core.config import Settings
from app.core.registry import AppRegistry


@pytest.mark.asyncio
async def test_legacy_and_registered_keys(tmp_path):
    registry = AppRegistry(str(tmp_path / "registry.db"))
    registry.initialize()
    app_id, key = registry.register("Ritmo")
    legacy = "x" * 40
    settings = Settings(api_key=legacy, device="cpu", compute="int8")
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, registry=registry))
    connection = HTTPConnection({"type": "http", "app": app, "headers": []})
    assert await identify_client(connection, key) == app_id
    assert await identify_client(connection, legacy) == "legacy"
    assert await identify_client(connection, "invalid") is None
    assert await identify_client(connection, None) is None
