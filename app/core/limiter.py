"""Shared process-local rate limiting."""

from hashlib import sha256
from ipaddress import ip_address

from slowapi import Limiter
from app.core.config import get_settings
from starlette.requests import HTTPConnection


def configured_limit(key: str) -> str:
    return get_settings().rate_limit


def client_key(connection: HTTPConnection, token: str | None = None) -> str:
    if token:
        return "key:" + sha256(token.encode("utf-8")).hexdigest()
    return client_ip(connection)


def client_ip(connection: HTTPConnection) -> str:
    if connection.app.state.settings.trust_proxy_headers:
        forwarded = connection.headers.get("CF-Connecting-IP")
        if forwarded:
            try:
                return "ip:" + str(ip_address(forwarded))
            except ValueError:
                pass
    return "ip:" + (connection.client.host if connection.client else "unknown")


def request_key(request) -> str:
    identity = getattr(request.state, "app_identity", None)
    if identity:
        return "app:" + identity
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    return client_key(request, token if scheme.lower() == "bearer" else None)


limiter = Limiter(key_func=request_key, storage_uri="memory://", headers_enabled=True)


def stream_client_key(connection: HTTPConnection, token: str) -> str:
    return client_key(connection, token) + ":" + client_ip(connection)
