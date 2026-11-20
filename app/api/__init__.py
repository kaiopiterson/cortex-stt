"""Aggregate API routers and register rate-limit error handling."""
from fastapi import APIRouter
from app.api.rest import router as rest_router
from app.api.websocket import router as websocket_router

api_router = APIRouter()
api_router.include_router(rest_router)
api_router.include_router(websocket_router)

# APIRouter inclusion does not propagate exception handlers. SlowAPI's
# exception is an HTTPException, so FastAPI's default handler returns 429.
