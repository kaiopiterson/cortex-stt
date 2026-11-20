"""Shared authentication for REST and streaming (CortexLab STT v0.3.0)."""

import asyncio
import hashlib
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Literal

from starlette.requests import HTTPConnection

from app.core.registry import AppRegistry, CredentialRecord


@dataclass(frozen=True)
class Identity:
    app_id: str
    kind: Literal["legacy", "permanent", "token"]
    subject: str | None = None


class CredentialStoreUnavailable(Exception):
    """The credential database could not be read (maps to HTTP 503)."""


class IdentityCache:
    """Short-lived cache of credential lookups, keyed by credential digest.

    Entries live for ``ttl`` seconds, so a revocation made by another process
    (for example the CLI) takes effect within that window. Administrative
    operations in this process call :meth:`clear` for immediate effect.
    """

    def __init__(self, ttl: float = 1.0, max_entries: int = 10_000):
        self.ttl = ttl
        self.max_entries = max_entries
        self._entries: dict[str, tuple[float, CredentialRecord | None]] = {}

    def get(self, digest: str) -> tuple[bool, CredentialRecord | None]:
        entry = self._entries.get(digest)
        if entry is None or time.monotonic() - entry[0] > self.ttl:
            return False, None
        return True, entry[1]

    def put(self, digest: str, record: CredentialRecord | None) -> None:
        if len(self._entries) >= self.max_entries:
            self._entries.clear()
        self._entries[digest] = (time.monotonic(), record)

    def clear(self) -> None:
        self._entries.clear()


def identity_cache(connection: HTTPConnection) -> IdentityCache:
    state = connection.app.state
    cache = getattr(state, "identity_cache", None)
    if cache is None:
        cache = IdentityCache()
        state.identity_cache = cache
    return cache


async def identify_client(
    connection: HTTPConnection, key: str | None, *, active_session: bool = False
) -> Identity | None:
    """Resolve a credential to an Identity.

    ``active_session`` ignores token expiry (but not revocation or rotation) for
    WebSocket sessions that were authenticated at handshake time.
    """
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
    if len(key) != 47 or not key.startswith(("stt_", "tok_")):
        return None
    cache = identity_cache(connection)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    hit, record = cache.get(digest)
    if not hit:
        try:
            record = await asyncio.to_thread(registry.lookup, key)
        except sqlite3.Error as exc:
            raise CredentialStoreUnavailable("Credential store unavailable") from exc
        cache.put(digest, record)
    if record is None:
        return None
    if record.kind == "permanent":
        return Identity(record.app_id, "permanent")
    if (
        not active_session
        and record.expires_at is not None
        and record.expires_at <= int(time.time())
    ):
        return None
    return Identity(record.app_id, "token", record.subject)
