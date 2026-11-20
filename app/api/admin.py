"""Administrative endpoints (CortexLab STT v0.3.0).

Enabled only when STT_ADMIN_KEY is set and reachable only from
STT_ADMIN_ALLOWED_NETWORKS. Every mutation is recorded in admin_audit.
"""

import asyncio
import json
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from pydantic import BaseModel, Field

from app.core.access import actor, admin_enabled, matches, network_allowed
from app.core.limiter import client_ip

router = APIRouter()
admin_failures = FixedWindowRateLimiter(MemoryStorage())
FAILURES = parse("30/minute")


async def require_admin(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    if not admin_enabled(request) or not network_allowed(request):
        raise HTTPException(404, "Not found")
    if not admin_failures.test(FAILURES, client_ip(request)):
        raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "60"})
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not matches(token, request.app.state.settings.admin_key):
        admin_failures.hit(FAILURES, client_ip(request))
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})


async def audit(request: Request, action: str, app_id: str | None, detail: dict | None = None):
    """Record an administrative mutation. Never include secrets in detail."""
    await asyncio.to_thread(
        request.app.state.registry.record_audit,
        action,
        app_id,
        actor(request),
        json.dumps(detail) if detail else None,
    )


@router.get("/v1/admin/usage", include_in_schema=False, dependencies=[Depends(require_admin)])
async def usage(request: Request, limit: Annotated[int, Query(ge=1, le=1000)] = 100):
    rows = await asyncio.to_thread(request.app.state.usage.report, limit)
    return JSONResponse({"data": rows}, headers={"Cache-Control": "no-store"})


class Registration(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class LimitsUpdate(BaseModel):
    """Per-application limits. Omitted fields are unchanged; null restores the default."""

    model_config = {"extra": "forbid"}

    requests_per_minute: int | None = Field(default=None, gt=0)
    daily_audio_seconds: int | None = Field(default=None, gt=0)
    monthly_audio_seconds: int | None = Field(default=None, gt=0)
    concurrency: int | None = Field(default=None, gt=0)
    priority_class: Literal["interactive", "normal", "batch"] | None = None


def invalidate_caches(request: Request, app_id: str | None = None) -> None:
    """Make credential and policy changes take effect immediately in this process."""
    cache = getattr(request.app.state, "identity_cache", None)
    if cache is not None:
        cache.clear()
    gate = getattr(request.app.state, "gateway", None)
    if gate is not None:
        gate.invalidate(app_id)


@router.get("/admin", include_in_schema=False)
async def dashboard(request: Request):
    # The page holds no secret: the operator types the admin key into it and
    # every API call it makes is authenticated. Only the network is checked here.
    if not admin_enabled(request) or not network_allowed(request):
        raise HTTPException(404, "Not found")
    html = await asyncio.to_thread(
        (Path(__file__).resolve().parents[1] / "admin.html").read_text, encoding="utf-8"
    )
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/v1/admin/apps", include_in_schema=False, dependencies=[Depends(require_admin)])
async def applications(request: Request):
    state = request.app.state
    rows = await asyncio.to_thread(state.registry.list_apps)
    settings = state.settings
    return JSONResponse(
        {
            "data": rows,
            "limits": {
                "requests_per_minute": settings.app_requests_per_minute,
                "daily_seconds": settings.app_daily_audio_seconds,
                "monthly_seconds": settings.app_monthly_audio_seconds,
                "concurrency": settings.app_concurrency,
                "sample_rate": settings.sample_rate,
            },
            "subject_limits": {
                "requests_per_minute": settings.subject_requests_per_minute,
                "daily_seconds": settings.subject_daily_audio_seconds,
                "monthly_seconds": settings.subject_monthly_audio_seconds,
                "concurrency": settings.subject_concurrency,
            },
            "active": dict(state.app_slots.counts),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/v1/admin/apps", include_in_schema=False, dependencies=[Depends(require_admin)])
async def register_app(request: Request, body: Registration):
    try:
        app_id, key = await asyncio.to_thread(request.app.state.registry.register, body.name)
    except ValueError as exc:
        raise HTTPException(422, "Invalid application name") from exc
    await audit(request, "register", app_id, {"name": body.name})
    return JSONResponse(
        {"id": app_id, "api_key": key}, status_code=201, headers={"Cache-Control": "no-store"}
    )


@router.post(
    "/v1/admin/apps/{app_id}/{action}",
    include_in_schema=False,
    dependencies=[Depends(require_admin)],
)
async def manage_app(request: Request, app_id: str, action: str):
    registry = request.app.state.registry
    rows = await asyncio.to_thread(registry.list_apps)
    if not any(row["id"] == app_id for row in rows):
        raise HTTPException(404, "Application not found")
    operations = {
        "rotate": registry.rotate,
        "revoke": registry.revoke_app,
        "revoke-tokens": registry.revoke_tokens,
    }
    if action not in operations:
        raise HTTPException(404, "Operation not found")
    try:
        result = await asyncio.to_thread(operations[action], app_id)
    except ValueError as exc:
        raise HTTPException(409, "Operation unavailable") from exc
    invalidate_caches(request, app_id)
    await audit(request, action, app_id)
    payload = {"id": app_id, "action": action}
    if action == "rotate":
        payload["api_key"] = result
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.put(
    "/v1/admin/apps/{app_id}/limits",
    include_in_schema=False,
    dependencies=[Depends(require_admin)],
)
async def update_limits(request: Request, app_id: str, body: LimitsUpdate):
    changes = body.model_dump(exclude_unset=True)
    if "priority_class" in changes and changes["priority_class"] is None:
        changes["priority_class"] = "normal"
    if not changes:
        raise HTTPException(422, "No limits supplied")
    registry = request.app.state.registry
    if not await asyncio.to_thread(registry.exists, app_id):
        raise HTTPException(404, "Application not found")
    try:
        await asyncio.to_thread(lambda: registry.set_limits(app_id, **changes))
    except ValueError as exc:
        raise HTTPException(422, "Invalid limits") from exc
    invalidate_caches(request, app_id)
    await audit(request, "set-limits", app_id, changes)
    return JSONResponse({"id": app_id, "limits": changes}, headers={"Cache-Control": "no-store"})


@router.get("/v1/admin/audit", include_in_schema=False, dependencies=[Depends(require_admin)])
async def audit_log(request: Request, limit: Annotated[int, Query(ge=1, le=1000)] = 100):
    rows = await asyncio.to_thread(request.app.state.registry.audit_events, limit)
    return JSONResponse({"data": rows}, headers={"Cache-Control": "no-store"})
