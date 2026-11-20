"""Request correlation, JSON logs and HTTP metrics (CortexLab STT v0.3.0, AUD-15).

Logs never include credentials, audio or transcripts. Each request gets a
``request_id`` (taken from a well-formed ``X-Request-ID`` header or generated),
returned in the response and attached to every log line written while the
request is being handled.
"""

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar

from app.core import telemetry

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")
access_logger = logging.getLogger("app.access")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line; extra fields come from ``record.fields``."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str, fmt: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s")
        )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def incoming_request_id(scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            candidate = value.decode("latin-1")
            if REQUEST_ID_PATTERN.fullmatch(candidate):
                return candidate
    return uuid.uuid4().hex


def route_template(scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class RequestContext:
    """Outermost ASGI middleware: correlation id, access log and HTTP metrics."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        request_id = incoming_request_id(scope)
        token = request_id_var.set(request_id)
        if scope["type"] == "websocket":
            try:
                await self.app(scope, receive, send)
            finally:
                request_id_var.reset(token)
            return
        started = time.perf_counter()
        status = 500

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            elapsed = time.perf_counter() - started
            route = route_template(scope)
            telemetry.http_requests.labels(route, scope.get("method", "GET"), str(status)).inc()
            telemetry.http_duration.labels(route).observe(elapsed)
            if route not in ("/metrics", "/health", "/ready"):
                access_logger.info(
                    "request",
                    extra={
                        "fields": {
                            "method": scope.get("method"),
                            "route": route,
                            "status": status,
                            "duration_ms": round(elapsed * 1000, 2),
                        }
                    },
                )
            request_id_var.reset(token)
