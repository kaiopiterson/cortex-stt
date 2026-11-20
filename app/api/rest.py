"""REST endpoints."""
import asyncio
import logging
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import Annotated
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from app.api.common import (SettingsDependency, InferenceDependency, Authenticated,
                            acquire_session, processing_metadata)
from app.core.limiter import limiter
from app.services.inference import (AudioDurationError, InvalidAudioError, InferenceBusyError,
                                    ModelNotReadyError, ModelExecutionError)

logger = logging.getLogger(__name__)
router = APIRouter()
INDEX_PATH = Path(__file__).resolve().parents[1] / "index.html"
SUPPORTED_AUDIO_TYPES = ("audio/", "video/")

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def web_interface() -> HTMLResponse:
    try:
        content = await asyncio.to_thread(INDEX_PATH.read_text, encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(404, "Web interface unavailable") from exc
    except OSError as exc:
        raise HTTPException(500, "Unable to load web interface") from exc
    return HTMLResponse(content=content)


@router.get("/health", tags=["Health"])
async def health(settings: SettingsDependency, inference: InferenceDependency) -> dict:
    return {"status": "ok" if inference.is_ready else "initializing",
            "ready": inference.is_ready, "version": settings.app_version,
            "model": settings.model, "device": settings.device}


@router.get("/v1/models", tags=["Models"])
@limiter.limit("60/minute")
async def list_models(request: Request, settings: SettingsDependency, authenticated: Authenticated) -> dict:
    return {"object": "list", "data": [{"id": settings.model, "object": "model", "owned_by": "cortexlab"}]}


@router.post("/v1/audio/transcriptions", tags=["Transcriptions"])
@limiter.limit("60/minute")
async def transcribe_audio(request: Request, settings: SettingsDependency,
                           inference: InferenceDependency, authenticated: Authenticated,
                           file: Annotated[UploadFile, File()],
                           model: Annotated[str | None, Form()] = None,
                           language: Annotated[str | None, Form()] = None,
                           prompt: Annotated[str | None, Form()] = None) -> dict:
    if (model or settings.model) not in {settings.model, "whisper-1"}:
        raise HTTPException(400, "Unsupported model")
    if not inference.is_ready:
        raise HTTPException(503, "Inference model is not ready")
    content_type = file.content_type or ""
    if content_type and content_type != "application/octet-stream" and not content_type.startswith(SUPPORTED_AUDIO_TYPES):
        raise HTTPException(415, "Unsupported audio content type")
    semaphore = request.app.state.rest_slots
    acquired = False
    try:
        await acquire_session(semaphore, settings.inference_queue_timeout_seconds)
        acquired = True
        started = monotonic()
        audio = await file.read(settings.max_upload_bytes + 1)
        if not audio:
            raise HTTPException(422, "Empty audio file")
        if len(audio) > settings.max_upload_bytes:
            raise HTTPException(413, "Maximum upload size exceeded")
        result = await inference.transcribe(audio=BytesIO(audio), language=language, initial_prompt=prompt)
        response = result.to_dict()
        response["total_ms"] = round((monotonic() - started) * 1000, 2)
        response["processing"] = processing_metadata(settings)
        return response
    except AudioDurationError as exc:
        raise HTTPException(413, "Maximum audio duration exceeded") from exc
    except InvalidAudioError as exc:
        raise HTTPException(422, "Invalid or unsupported audio") from exc
    except InferenceBusyError as exc:
        raise HTTPException(503, "Inference capacity unavailable", headers={"Retry-After": "2"}) from exc
    except ModelNotReadyError as exc:
        raise HTTPException(503, "Inference model is not ready") from exc
    except ModelExecutionError as exc:
        logger.error("REST inference execution failed")
        raise HTTPException(500, "Transcription failed") from exc
    finally:
        if acquired:
            semaphore.release()
        await file.close()

