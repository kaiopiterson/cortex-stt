"""Read-only operational endpoints, disabled without a separate admin secret."""
import asyncio
import secrets
from pathlib import Path
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, JSONResponse

from app.core.telemetry import telemetry
from app.core.limiter import client_ip
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from app.services.gpu_info import gpu_metrics

router = APIRouter()
admin_failures = FixedWindowRateLimiter(MemoryStorage())


async def require_admin(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    secret = request.app.state.settings.admin_key
    if secret is None:
        raise HTTPException(404, "Not found")
    if not admin_failures.test(parse("30/minute"), client_ip(request)):
        raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "60"})
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        token.encode(), secret.get_secret_value().encode()
    ):
        admin_failures.hit(parse("30/minute"), client_ip(request))
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})


@router.get("/metrics", include_in_schema=False, dependencies=[Depends(require_admin)])
async def metrics(request: Request) -> PlainTextResponse:
    state = request.app.state
    gauges = state.inference_service.metrics_snapshot()
    gauges["stt_registered_app_operations_active"] = sum(state.app_slots.counts.values())
    gauges["stt_stream_sessions_active"] = sum(getattr(state, "stream_key_counts", {}).values())
    if state.settings.device == "cuda":
        gauges.update(await asyncio.to_thread(gpu_metrics, state.settings.device_index))
    else:
        gauges["stt_gpu_available"] = 0
    return PlainTextResponse(
        telemetry.render(gauges), media_type="text/plain; version=0.0.4",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/v1/admin/usage", include_in_schema=False, dependencies=[Depends(require_admin)])
async def usage(request: Request, limit: Annotated[int, Query(ge=1, le=1000)] = 100):
    rows = await asyncio.to_thread(request.app.state.usage.report, limit)
    return JSONResponse({"data": rows}, headers={"Cache-Control": "no-store"})


class Registration(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.get("/admin", include_in_schema=False)
async def dashboard(request: Request):
    if request.app.state.settings.admin_key is None:
        raise HTTPException(404, "Not found")
    html = await asyncio.to_thread(
        (Path(__file__).resolve().parents[1] / "admin.html").read_text, encoding="utf-8"
    )
    return HTMLResponse(html, headers={
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/v1/admin/apps", include_in_schema=False, dependencies=[Depends(require_admin)])
async def applications(request: Request):
    state = request.app.state
    rows = await asyncio.to_thread(state.registry.list_apps)
    settings = state.settings
    return JSONResponse({
        "data": rows,
        "limits": {
            "requests_per_minute": settings.app_requests_per_minute,
            "daily_seconds": settings.app_daily_audio_seconds,
            "monthly_seconds": settings.app_monthly_audio_seconds,
            "concurrency": settings.app_concurrency,
            "sample_rate": settings.sample_rate,
        },
        "active": dict(state.app_slots.counts),
    }, headers={"Cache-Control": "no-store"})


@router.post("/v1/admin/apps", include_in_schema=False, dependencies=[Depends(require_admin)])
async def register_app(request: Request, body: Registration):
    try:
        app_id, key = await asyncio.to_thread(request.app.state.registry.register, body.name)
    except ValueError as exc:
        raise HTTPException(422, "Invalid application name") from exc
    return JSONResponse({"id": app_id, "api_key": key}, status_code=201,
                        headers={"Cache-Control": "no-store"})


@router.post("/v1/admin/apps/{app_id}/{action}", include_in_schema=False,
             dependencies=[Depends(require_admin)])
async def manage_app(request: Request, app_id: str, action: str):
    registry = request.app.state.registry
    rows = await asyncio.to_thread(registry.list_apps)
    if not any(row["id"] == app_id for row in rows):
        raise HTTPException(404, "Application not found")
    operations = {"rotate": registry.rotate, "revoke": registry.revoke_app,
                  "revoke-tokens": registry.revoke_tokens}
    if action not in operations:
        raise HTTPException(404, "Operation not found")
    try:
        result = await asyncio.to_thread(operations[action], app_id)
    except ValueError as exc:
        raise HTTPException(409, "Operation unavailable") from exc
    payload = {"id": app_id, "action": action}
    if action == "rotate":
        payload["api_key"] = result
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})
