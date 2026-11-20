"""Authenticate and bound request bodies before parsing (CortexLab STT v0.3.0)."""

from app.core import telemetry
from app.core.auth import CredentialStoreUnavailable, identify_client
from app.api.admin import require_admin
from app.api.metrics import require_metrics
from starlette.requests import Request
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from starlette.requests import HTTPConnection
from app.core.limiter import client_ip
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class BodyLimit:
    def __init__(self, app):
        self.app = app
        self.unauthorized = FixedWindowRateLimiter(MemoryStorage())

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings = scope["app"].state.settings
        path = scope["path"]
        # Operational routes are authenticated before any body is read.
        guard = None
        if path == "/metrics":
            guard = require_metrics
        elif path == "/v1/admin" or path.startswith("/v1/admin/"):
            guard = require_admin
        if guard is not None:
            try:
                await guard(Request(scope), HTTPConnection(scope).headers.get("Authorization"))
            except HTTPException as exc:
                if exc.status_code == 401:
                    telemetry.auth_failed("metrics" if path == "/metrics" else "admin")
                await JSONResponse(
                    {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
                )(scope, receive, send)
                return
        connection = HTTPConnection(scope)
        scheme, _, token = connection.headers.get("Authorization", "").partition(" ")
        identity = None
        if path == "/v1/audio/transcriptions" and scheme.lower() == "bearer":
            try:
                identity = await identify_client(connection, token)
            except CredentialStoreUnavailable:
                telemetry.reject("unavailable")
                await JSONResponse(
                    {"detail": "Service temporarily unavailable"},
                    status_code=503,
                    headers={"Retry-After": "2"},
                )(scope, receive, send)
                return
            # Reused by the route dependency: one credential lookup per request.
            connection.state.identity = identity
        if path == "/v1/audio/transcriptions" and identity is None:
            allowed = self.unauthorized.hit(parse("60/minute"), client_ip(connection))
            telemetry.auth_failed("rest")
            telemetry.reject("unauthorized" if allowed else "rate_limited")
            response = JSONResponse(
                {"detail": "Invalid API credentials" if allowed else "Rate limit exceeded"},
                status_code=401 if allowed else 429,
                headers={"WWW-Authenticate": "Bearer"} if allowed else {"Retry-After": "60"},
            )
            await response(scope, receive, send)
            return
        if path == "/v1/audio/transcriptions" and token.startswith("stt_"):
            telemetry.reject("forbidden_scope")
            await JSONResponse({"detail": "Forbidden credential scope"}, status_code=403)(
                scope, receive, send
            )
            return
        limit = settings.max_upload_bytes + 65536 if path == "/v1/audio/transcriptions" else 65536
        headers = dict(scope["headers"])
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await JSONResponse({"detail": "Invalid content length"}, status_code=400)(
                scope, receive, send
            )
            return
        if declared > limit:
            await JSONResponse({"detail": "Maximum upload size exceeded"}, status_code=413)(
                scope, receive, send
            )
            return
        count = 0

        async def bounded_receive():
            nonlocal count
            message = await receive()
            count += len(message.get("body", b""))
            if count > limit:
                raise HTTPException(413, "Maximum upload size exceeded")
            return message

        await self.app(scope, bounded_receive, send)
