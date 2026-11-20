"""Aggregate API routers; exception handlers are registered in main."""

from fastapi import APIRouter

from app.api.admin import router as admin_router
from app.api.metrics import router as metrics_router
from app.api.rest import router as rest_router
from app.api.tokens import router as token_router
from app.api.websocket import router as websocket_router


def build_router(include_admin: bool = False, include_metrics: bool | None = None) -> APIRouter:
    router = APIRouter()
    if include_admin:
        router.include_router(admin_router)
    if include_admin if include_metrics is None else include_metrics:
        router.include_router(metrics_router)
    router.include_router(token_router)
    router.include_router(rest_router)
    router.include_router(websocket_router)
    return router


# Compatibility for independently constructed test applications.
api_router = build_router(include_admin=True)
