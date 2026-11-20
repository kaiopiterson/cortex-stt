"""Shared API dependencies and helpers."""
import asyncio
import secrets
from typing import Annotated
from fastapi import Depends, Header, HTTPException, Request
from app.core.config import Settings
from app.services.inference import InferenceService, InferenceBusyError

def get_settings_from_state(request: Request) -> Settings:
    return request.app.state.settings


def get_inference_service(request: Request) -> InferenceService:
    return request.app.state.inference_service


SettingsDependency = Annotated[Settings, Depends(get_settings_from_state)]
InferenceDependency = Annotated[InferenceService, Depends(get_inference_service)]


def validate_api_key(supplied_key: str | None, settings: Settings) -> bool:
    return bool(supplied_key and secrets.compare_digest(
        supplied_key.encode("utf-8"), settings.api_key.get_secret_value().encode("utf-8")))


async def require_api_key(settings: SettingsDependency,
                    authorization: Annotated[str | None, Header()] = None) -> None:
    scheme, separator, token = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not validate_api_key(token, settings):
        raise HTTPException(401, "Invalid API credentials" if authorization else "Missing API credentials",
                            headers={"WWW-Authenticate": "Bearer"})


Authenticated = Annotated[None, Depends(require_api_key)]

def processing_metadata(settings: Settings) -> dict:
    return {"mode": "server", "location": "server", "server_used": True,
            "engine": "faster-whisper", "model": settings.model, "device": settings.device}

async def acquire_session(semaphore: asyncio.Semaphore, timeout: float) -> None:
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise InferenceBusyError("Maximum concurrent sessions reached") from exc


def valid_language(value: str) -> bool:
    from faster_whisper.tokenizer import _LANGUAGE_CODES
    return len(value) <= 32 and (value.lower() in {"", "auto"} or
        value.lower().replace("_", "-").split("-")[0] in _LANGUAGE_CODES)
