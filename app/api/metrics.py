"""Prometheus metrics endpoint (CortexLab STT v0.3.0, AUD-10).

Accepts STT_METRICS_TOKEN (read-only, for Prometheus) or STT_ADMIN_KEY, and
only from STT_ADMIN_ALLOWED_NETWORKS. The metrics token is rejected on every
administrative route.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter

from app.core.access import matches, metrics_enabled, network_allowed
from app.core.limiter import client_ip
from app.core import telemetry
from app.services.gpu_info import gpu_metrics

router = APIRouter()
metrics_failures = FixedWindowRateLimiter(MemoryStorage())
FAILURES = parse("30/minute")


async def require_metrics(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    if not metrics_enabled(request) or not network_allowed(request):
        raise HTTPException(404, "Not found")
    if not metrics_failures.test(FAILURES, client_ip(request)):
        raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "60"})
    settings = request.app.state.settings
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not (
        matches(token, settings.metrics_token) or matches(token, settings.admin_key)
    ):
        metrics_failures.hit(FAILURES, client_ip(request))
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})


@router.get("/metrics", include_in_schema=False, dependencies=[Depends(require_metrics)])
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
        telemetry.render(gauges),
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )
