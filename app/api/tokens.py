"""Exchange backend application credentials for short-lived access tokens."""

import asyncio
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from pydantic import BaseModel, Field

from app.core import telemetry
from app.core.limiter import client_ip
from app.core.registry import AppRegistry

router = APIRouter()


class TokenRequest(BaseModel):
    subject: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$")


def token_rate_key(request: Request) -> str:
    return client_ip(request)


@router.post("/v1/auth/token")
async def issue_token(
    request: Request,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    body: TokenRequest | None = None,
):
    state = request.app.state
    if not hasattr(state, "token_limits"):
        state.token_limits = FixedWindowRateLimiter(MemoryStorage())
    limits = state.token_limits
    ip = token_rate_key(request)
    settings = state.settings
    failure_limit = parse(f"{settings.token_failures_per_minute}/minute")
    if not limits.test(failure_limit, "failure", ip):
        telemetry.reject("rate_limited")
        raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "60"})
    scheme, _, key = (authorization or "").partition(" ")
    registry: AppRegistry | None = getattr(state, "registry", None)
    app_id = None
    if (
        scheme.lower() == "bearer"
        and registry is not None
        and key.startswith("stt_")
        and len(key) == 47
    ):
        try:
            app_id = await asyncio.to_thread(registry.identify, key)
        except sqlite3.Error as exc:
            raise HTTPException(
                503, "Service temporarily unavailable", headers={"Retry-After": "2"}
            ) from exc
    if registry is None or app_id is None:
        limits.hit(failure_limit, "failure", ip)
        telemetry.auth_failed("token")
        raise HTTPException(401, "Invalid API credentials", headers={"WWW-Authenticate": "Bearer"})
    if not limits.hit(parse(f"{settings.token_issuance_per_minute}/minute"), "issuance", app_id):
        telemetry.reject("rate_limited")
        raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "60"})
    try:
        result = await asyncio.to_thread(
            registry.issue_token, key, subject=body.subject if body else None
        )
    except sqlite3.Error as exc:
        raise HTTPException(
            503, "Service temporarily unavailable", headers={"Retry-After": "2"}
        ) from exc
    if result is None:
        raise HTTPException(401, "Invalid API credentials", headers={"WWW-Authenticate": "Bearer"})
    token, ttl = result
    telemetry.tokens_issued.inc()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {"access_token": token, "token_type": "bearer", "expires_in": ttl}
