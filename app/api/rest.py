"""REST endpoints (CortexLab STT v0.3.0)."""

import asyncio
import json
import logging
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from app.api.common import (
    Authenticated,
    InferenceDependency,
    SettingsDependency,
    acquire_session,
    current_admission,
    parse_hotwords,
    processing_metadata,
    valid_language,
)
from app.core import telemetry
from app.core.auth import CredentialStoreUnavailable
from app.core.gateway import gateway
from app.core.limiter import configured_limit, limiter
from app.core.usage import Reservation, UsageLimit
from app.services.inference import (
    AudioDurationError,
    InvalidAudioError,
    InferenceBusyError,
    ModelNotReadyError,
    ModelExecutionError,
)

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
    return {
        "status": "ok" if inference.is_ready else "initializing",
        "ready": inference.is_ready,
        "version": settings.app_version,
        "model": settings.model,
        "device": settings.device,
    }


@router.get("/v1/models", tags=["Models"])
@limiter.limit(configured_limit)
async def list_models(
    request: Request, response: Response, settings: SettingsDependency, authenticated: Authenticated
) -> dict:
    return {
        "object": "list",
        "data": [{"id": settings.model, "object": "model", "owned_by": "cortexlab"}],
    }


@router.post("/v1/audio/transcriptions", tags=["Transcriptions"])
@limiter.limit(configured_limit)
async def transcribe_audio(
    request: Request,
    response: Response,
    settings: SettingsDependency,
    inference: InferenceDependency,
    authenticated: Authenticated,
    file: Annotated[UploadFile, File()],
    model: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = None,
    prompt: Annotated[str | None, Form()] = None,
    mode: Annotated[str, Form()] = "dictation",
    hotwords: Annotated[str | None, Form()] = None,
) -> dict:
    try:
        hints = parse_hotwords(json.loads(hotwords) if hotwords is not None else None)
    except ValueError as exc:
        raise HTTPException(422, "Invalid hotwords") from exc
    if mode not in ("command", "dictation"):
        raise HTTPException(422, "Invalid transcription mode")
    if language is not None and not valid_language(language):
        raise HTTPException(422, "Invalid language")
    if (model or settings.model) not in {settings.model, "whisper-1"}:
        raise HTTPException(400, "Unsupported model")
    if not inference.is_ready:
        raise HTTPException(503, "Inference model is not ready")
    content_type = file.content_type or ""
    if (
        content_type
        and content_type != "application/octet-stream"
        and not content_type.startswith(SUPPORTED_AUDIO_TYPES)
    ):
        raise HTTPException(415, "Unsupported audio content type")
    semaphore = request.app.state.rest_slots
    acquired = False
    admission = current_admission(request)
    gate = gateway(request)
    reservation: Reservation | None = None
    try:
        gate.hold(admission)
        await acquire_session(semaphore, settings.inference_queue_timeout_seconds)
        acquired = True
        started = monotonic()
        audio = await file.read(settings.max_upload_bytes + 1)
        if not audio:
            raise HTTPException(422, "Empty audio file")
        if len(audio) > settings.max_upload_bytes:
            raise HTTPException(413, "Maximum upload size exceeded")
        samples = await asyncio.to_thread(inference.prepare_audio, BytesIO(audio))
        reservation = await gate.charge_samples(admission, int(samples.size))
        result = await inference.transcribe(
            audio=samples,
            language=language,
            initial_prompt=prompt,
            priority=admission.priority(interactive=mode == "command"),
            hotwords=hints,
        )
        payload = result.to_dict()
        payload["needs_review"] = (
            not result.text.strip()
            or result.confidence < settings.review_threshold
            or result.filtered_segments > 0
        )
        payload["total_ms"] = round((monotonic() - started) * 1000, 2)
        payload["processing"] = processing_metadata(settings)
        return payload
    except UsageLimit as exc:
        telemetry.reject("quota_exceeded")
        raise HTTPException(429, "Application usage limit exceeded") from exc
    except CredentialStoreUnavailable as exc:
        telemetry.reject("unavailable")
        raise HTTPException(
            503, "Service temporarily unavailable", headers={"Retry-After": "2"}
        ) from exc
    except AudioDurationError as exc:
        raise HTTPException(413, "Maximum audio duration exceeded") from exc
    except InvalidAudioError as exc:
        raise HTTPException(422, "Invalid or unsupported audio") from exc
    except InferenceBusyError as exc:
        gate.refund(reservation)
        telemetry.reject("server_busy")
        raise HTTPException(
            503, "Inference capacity unavailable", headers={"Retry-After": "2"}
        ) from exc
    except ModelNotReadyError as exc:
        gate.refund(reservation)
        raise HTTPException(503, "Inference model is not ready") from exc
    except ModelExecutionError as exc:
        gate.refund(reservation)
        logger.error("REST inference execution failed")
        raise HTTPException(500, "Transcription failed") from exc
    finally:
        if acquired:
            semaphore.release()
        gate.release(admission)
        await file.close()
