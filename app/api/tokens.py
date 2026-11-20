"""Exchange backend application credentials for short-lived access tokens."""
import asyncio
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, Response

from app.core.limiter import client_ip, limiter

router = APIRouter()


def token_rate_key(request: Request) -> str:
    return client_ip(request)


@router.post("/v1/auth/token")
@limiter.limit("30/minute", key_func=token_rate_key)
async def issue_token(
    request: Request,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
):
    scheme, _, key = (authorization or "").partition(" ")
    registry = getattr(request.app.state, "registry", None)
    result = None
    if scheme.lower() == "bearer" and registry is not None and len(key) == 47:
        result = await asyncio.to_thread(registry.issue_token, key)
    if result is None:
        raise HTTPException(401, "Invalid API credentials", headers={"WWW-Authenticate": "Bearer"})
    token, ttl = result
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {"access_token": token, "token_type": "bearer", "expires_in": ttl}
