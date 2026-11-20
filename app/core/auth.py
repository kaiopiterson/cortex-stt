"""Shared authentication for REST and streaming."""

import asyncio
import secrets
from dataclasses import dataclass
from typing import Literal

from starlette.requests import HTTPConnection

from app.core.registry import AppRegistry


@dataclass(frozen=True)
class Identity:
    app_id: str
    kind: Literal["legacy", "permanent", "token"]


async def identify_client(
    connection: HTTPConnection, key: str | None, *, active_session: bool = False
) -> Identity | None:
    if not key or len(key) > 4096:
        return None
    settings = connection.app.state.settings
    if secrets.compare_digest(
        key.encode("utf-8"), settings.api_key.get_secret_value().encode("utf-8")
    ):
        return Identity("legacy", "legacy") if settings.legacy_api_key_enabled else None
    registry: AppRegistry | None = getattr(connection.app.state, "registry", None)
    if registry is None:
        return None
    app_id = await asyncio.to_thread(registry.identify, key, ignore_expiry=active_session)
    if app_id is None:
        return None
    return Identity(app_id, "permanent" if key.startswith("stt_") else "token")
