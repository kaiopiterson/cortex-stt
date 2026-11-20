"""Shared authentication for REST and streaming."""
import asyncio
import secrets

from starlette.requests import HTTPConnection

from app.core.registry import AppRegistry


async def identify_client(connection: HTTPConnection, key: str | None) -> str | None:
    if not key or len(key) > 4096:
        return None
    settings = connection.app.state.settings
    if secrets.compare_digest(
        key.encode("utf-8"), settings.api_key.get_secret_value().encode("utf-8")
    ):
        return "legacy"
    registry: AppRegistry | None = getattr(connection.app.state, "registry", None)
    if registry is None:
        return None
    return await asyncio.to_thread(registry.identify, key)
