"""Read-only operational endpoints, disabled without a separate admin secret."""
import asyncio
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, JSONResponse

from app.core.telemetry import telemetry
from app.services.gpu_info import gpu_metrics

router = APIRouter()


async def require_admin(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    secret = request.app.state.settings.admin_key
    if secret is None:
        raise HTTPException(404, "Not found")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        token.encode(), secret.get_secret_value().encode()
    ):
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
