"""Exchange backend application credentials for short-lived access tokens."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from pydantic import BaseModel, Field

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
    failure_limit = parse("30/minute")
    if not limits.test(failure_limit, "failure", ip):
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
        app_id = await asyncio.to_thread(registry.identify, key)
    if registry is None or app_id is None:
        limits.hit(failure_limit, "failure", ip)
        raise HTTPException(401, "Invalid API credentials", headers={"WWW-Authenticate": "Bearer"})
    if not limits.hit(parse("30/minute"), "issuance", app_id):
        raise HTTPException(429, "Rate limit exceeded", headers={"Retry-After": "60"})
    result = await asyncio.to_thread(
        registry.issue_token, key, subject=body.subject if body else None
    )
    if result is None:
        raise HTTPException(401, "Invalid API credentials", headers={"WWW-Authenticate": "Bearer"})
    token, ttl = result
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {"access_token": token, "token_type": "bearer", "expires_in": ttl}
