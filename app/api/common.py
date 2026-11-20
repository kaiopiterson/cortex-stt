"""Shared API dependencies and helpers (CortexLab STT v0.3.0)."""

import asyncio
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.core.auth import CredentialStoreUnavailable, Identity, identify_client
from app.core.config import Settings
from app.core.gateway import Admission, gateway
from app.core.usage import UsageLimit, scope_key
from app.services.inference import InferenceBusyError, InferenceService


def get_settings_from_state(request: Request) -> Settings:
    return request.app.state.settings


def get_inference_service(request: Request) -> InferenceService:
    return request.app.state.inference_service


SettingsDependency = Annotated[Settings, Depends(get_settings_from_state)]
InferenceDependency = Annotated[InferenceService, Depends(get_inference_service)]


def validate_api_key(supplied_key: str | None, settings: Settings) -> bool:
    return bool(
        supplied_key
        and secrets.compare_digest(
            supplied_key.encode("utf-8"), settings.api_key.get_secret_value().encode("utf-8")
        )
    )


def unavailable() -> HTTPException:
    return HTTPException(503, "Service temporarily unavailable", headers={"Retry-After": "2"})


async def resolve_identity(request: Request, authorization: str | None) -> Identity | None:
    """Return the identity resolved by BodyLimit for this request, or resolve it now."""
    cached = getattr(request.state, "identity", None)
    if isinstance(cached, Identity):
        return cached
    scheme, separator, token = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    try:
        identity = await identify_client(request, token)
    except CredentialStoreUnavailable as exc:
        raise unavailable() from exc
    request.state.identity = identity
    return identity


async def require_api_key(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> None:
    identity = await resolve_identity(request, authorization)
    if identity is None:
        raise HTTPException(
            401,
            "Invalid API credentials" if authorization else "Missing API credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if identity.kind == "permanent":
        raise HTTPException(403, "Forbidden credential scope")
    request.state.app_identity = scope_key(identity.app_id, identity.subject)
    try:
        request.state.admission = await gateway(request).admit(identity, hold_slot=False)
    except UsageLimit as exc:
        raise HTTPException(429, "Application usage limit exceeded") from exc
    except CredentialStoreUnavailable as exc:
        raise unavailable() from exc


Authenticated = Annotated[None, Depends(require_api_key)]


def current_admission(request: Request) -> Admission:
    return request.state.admission


def processing_metadata(settings: Settings) -> dict:
    return {
        "mode": "server",
        "location": "server",
        "server_used": True,
        "engine": "faster-whisper",
        "model": settings.model,
        "device": settings.device,
    }


async def acquire_session(semaphore: asyncio.Semaphore, timeout: float) -> None:
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise InferenceBusyError("Maximum concurrent sessions reached") from exc


def valid_language(value: str) -> bool:
    from faster_whisper.tokenizer import _LANGUAGE_CODES

    return len(value) <= 32 and (
        value.lower() in {"", "auto"}
        or value.lower().replace("_", "-").split("-")[0] in _LANGUAGE_CODES
    )


def parse_hotwords(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("Invalid hotwords")
    if any(not isinstance(word, str) or not word.strip() or len(word) > 64 for word in value):
        raise ValueError("Invalid hotwords")
    return " ".join(value) or None
