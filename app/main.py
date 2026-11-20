"""CortexLab STT v0.2.0 — application orchestration."""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import api_router
from app.core.config import get_settings
from app.core.limiter import limiter
from app.services.inference import InferenceService

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logger.info("Starting %s version %s", settings.app_name, settings.app_version)
    
    inference_service = InferenceService(settings=settings)
    app.state.settings = settings
    app.state.inference_service = inference_service
    app.state.stream_slots = asyncio.Semaphore(settings.stream_sessions or settings.sessions)
    app.state.rest_slots = asyncio.Semaphore(settings.rest_concurrency)
    app.state.limiter = limiter
    
    try:
        await inference_service.initialize()
        logger.info(
            "CortexLab STT ready: model=%s device=%s workers=%s sessions=%s",
            settings.model, settings.device, settings.workers, settings.sessions
        )
        yield
    finally:
        logger.info("Stopping CortexLab STT")
        await inference_service.close()
        logger.info("CortexLab STT stopped")

def create_app() -> FastAPI:
    application = FastAPI(
        title="CortexLab STT Engine", 
        version="0.2.0",
        description="CortexLab Speech-to-Text Engine. REST and WebSocket transcription API designed for server-side inference and future hybrid edge integration.",
        lifespan=lifespan, 
        docs_url="/docs", 
        redoc_url="/redoc", 
        openapi_url="/openapi.json",
    )
    
    # Integração do Rate Limiting (SlowAPI)
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    
    # Registro do router consolidado
    application.include_router(api_router)
    return application

app = create_app()
