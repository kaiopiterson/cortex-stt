"""Shared process-local rate limiting."""
from contextvars import ContextVar
from hashlib import sha256
from ipaddress import ip_address

from slowapi import Limiter
from starlette.requests import HTTPConnection

rate_limit_value: ContextVar[str] = ContextVar("rate_limit_value", default="60/minute")


def configured_limit() -> str:
    return rate_limit_value.get()


def client_key(connection: HTTPConnection, token: str | None = None) -> str:
    if token:
        return "key:" + sha256(token.encode("utf-8")).hexdigest()
    if connection.app.state.settings.trust_proxy_headers:
        forwarded = connection.headers.get("CF-Connecting-IP")
        if forwarded:
            try:
                return "ip:" + str(ip_address(forwarded))
            except ValueError:
                pass
    return "ip:" + (connection.client.host if connection.client else "unknown")


def request_key(request) -> str:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    return client_key(request, token if scheme.lower() == "bearer" else None)


limiter = Limiter(key_func=request_key, storage_uri="memory://", headers_enabled=True)
