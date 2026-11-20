from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.body_limit import BodyLimit
from app.core.config import Settings


@pytest.mark.asyncio
async def test_unauthenticated_upload_never_reads_body():
    settings = Settings(api_key="x" * 40, device="cpu", compute="int8")
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))
    scope = {
        "type": "http",
        "path": "/v1/audio/transcriptions",
        "app": app,
        "headers": [],
        "client": ("192.0.2.1", 1234),
    }
    downstream = AsyncMock()
    receive = AsyncMock()
    send = AsyncMock()
    await BodyLimit(downstream)(scope, receive, send)
    receive.assert_not_awaited()
    downstream.assert_not_awaited()
    assert send.await_args_list[0].args[0]["status"] == 401
