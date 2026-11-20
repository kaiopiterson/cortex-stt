"""Limit upload bodies before multipart parsing."""
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/v1/audio/transcriptions":
            await self.app(scope, receive, send)
            return
        limit = scope["app"].state.settings.max_upload_bytes + 65536
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
