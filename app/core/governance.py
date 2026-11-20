"""Gateway admission helpers; legacy clients retain their existing limits."""

import asyncio

from starlette.requests import HTTPConnection

from app.core.usage import AppSlots, UsageStore


def registered(identity: str | None) -> bool:
    return identity is not None


async def charge(
    connection: HTTPConnection, identity: str | None, requests: int = 0, samples: int = 0
) -> None:
    if identity is None or not registered(identity):
        return
    settings = connection.app.state.settings
    store: UsageStore | None = getattr(connection.app.state, "usage", None)
    if store is None:
        return
    await asyncio.to_thread(
        store.charge,
        identity,
        requests,
        samples,
        settings.app_requests_per_minute,
        settings.app_daily_audio_seconds * settings.sample_rate,
        settings.app_monthly_audio_seconds * settings.sample_rate,
    )


def slots(connection: HTTPConnection) -> AppSlots:
    return connection.app.state.app_slots
