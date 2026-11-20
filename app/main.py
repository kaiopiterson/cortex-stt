"""CortexLab STT v0.3.0 — application orchestration."""

import asyncio
import logging
from collections.abc import Callable
from typing import cast
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import build_router
from app.core.body_limit import BodyLimit
from app.core.config import get_settings
from app.core.registry import AppRegistry
from app.core.auth import IdentityCache
from app.core.gateway import Gateway
from app.core.usage import AppSlots, UsageLedger, UsageStore
from app.core.limiter import limiter
from app.services.inference import InferenceService

logger = logging.getLogger(__name__)


async def maintenance_loop(app: FastAPI) -> None:
    """Persist usage in batches; the database stays off the request hot path."""
    settings = app.state.settings
    while True:
        await asyncio.sleep(settings.usage_flush_seconds)
        await app.state.ledger.flush()
        app.state.ledger.forget_stale()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logger.info("Starting %s version %s", settings.app_name, settings.app_version)

    inference_service = InferenceService(settings=settings)
    app.state.settings = settings
    app.state.registry = AppRegistry(settings.registry_path)
    await asyncio.to_thread(app.state.registry.initialize)
    app.state.usage = UsageStore(app.state.registry)
    await asyncio.to_thread(app.state.usage.initialize)
    app.state.app_slots = AppSlots()
    app.state.ledger = UsageLedger(app.state.usage)
    app.state.identity_cache = IdentityCache()
    app.state.gateway = Gateway(settings, app.state.registry, app.state.ledger, app.state.app_slots)
    app.state.inference_service = inference_service
    app.state.stream_slots = asyncio.Semaphore(settings.stream_sessions or settings.sessions)
    app.state.rest_slots = asyncio.Semaphore(settings.rest_concurrency)
    app.state.limiter = limiter

    maintenance = asyncio.create_task(maintenance_loop(app), name="stt-maintenance")
    try:
        await inference_service.initialize()
        logger.info(
            "CortexLab STT ready: model=%s device=%s workers=%s sessions=%s",
            settings.model,
            settings.device,
            settings.workers,
            settings.sessions,
        )
        yield
    finally:
        logger.info("Stopping CortexLab STT")
        maintenance.cancel()
        await asyncio.gather(maintenance, return_exceptions=True)
        await app.state.ledger.flush()
        await inference_service.close()
        logger.info("CortexLab STT stopped")


def create_app() -> FastAPI:
    production = get_settings().environment == "production"
    application = FastAPI(
        title="CortexLab STT Engine",
        version="0.3.0",
        description="CortexLab Speech-to-Text Engine. REST and WebSocket transcription API designed for server-side inference and future hybrid edge integration.",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )

    # Integração do Rate Limiting (SlowAPI)
    application.state.limiter = limiter
    application.add_exception_handler(
        RateLimitExceeded,
        cast(Callable[[Request, Exception], Response], _rate_limit_exceeded_handler),
    )

    # Registro do router consolidado
    application.add_middleware(BodyLimit)
    application.include_router(build_router(include_admin=get_settings().admin_key is not None))
    return application


app = create_app()
