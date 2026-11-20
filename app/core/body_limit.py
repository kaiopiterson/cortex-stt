"""Limit upload bodies before multipart parsing."""
from app.core.auth import identify_client
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
        if scope["type"] != "http" or scope["path"] != "/v1/audio/transcriptions":
            await self.app(scope, receive, send)
            return
        settings = scope["app"].state.settings
        connection = HTTPConnection(scope)
        scheme, _, token = connection.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not await identify_client(connection, token):
            allowed = self.unauthorized.hit(parse("60/minute"), client_ip(connection))
            response = JSONResponse(
                {"detail": "Invalid API credentials" if allowed else "Rate limit exceeded"},
                status_code=401 if allowed else 429,
                headers={"WWW-Authenticate": "Bearer"} if allowed else {"Retry-After": "60"},
            )
            await response(scope, receive, send)
            return
        limit = settings.max_upload_bytes + 65536
        headers = dict(scope["headers"])
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            await JSONResponse({"detail": "Invalid content length"}, status_code=400)(scope, receive, send)
            return
        if declared > limit:
            await JSONResponse({"detail": "Maximum upload size exceeded"}, status_code=413)(scope, receive, send)
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
