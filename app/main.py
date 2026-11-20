"""CortexLab STT v0.2.0 — application orchestration."""
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.routes import router
from app.core.config import get_settings
from app.services.inference import InferenceService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info("Starting %s version %s", settings.app_name, settings.app_version)
    inference_service = InferenceService(settings=settings)
    app.state.settings = settings
    app.state.inference_service = inference_service
    app.state.session_slots = asyncio.Semaphore(settings.sessions)
    try:
        await inference_service.initialize()
        logger.info("CortexLab STT ready: model=%s device=%s workers=%s sessions=%s",
                    settings.model, settings.device, settings.workers, settings.sessions)
        yield
    finally:
        logger.info("Stopping CortexLab STT")
        await inference_service.close()
        logger.info("CortexLab STT stopped")


def create_app() -> FastAPI:
    application = FastAPI(
        title="CortexLab STT Engine", version="0.2.0",
        description="CortexLab Speech-to-Text Engine. REST and WebSocket transcription API designed for server-side inference and future hybrid edge integration.",
        lifespan=lifespan, docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json",
    )
    application.include_router(router)
    return application


app = create_app()
