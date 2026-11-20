"""CortexLab STT v0.2.0 — shared process-local REST rate limiting."""
from slowapi import Limiter
from slowapi.util import get_remote_address

__all__ = ["limiter"]

limiter: Limiter = Limiter(
    key_func=get_remote_address,
    storage_uri="memory://",
    default_limits=[],
    headers_enabled=False,
    auto_check=True,
    enabled=True,
)
