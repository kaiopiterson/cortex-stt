"""Aggregate API routers; exception handlers are registered in main."""
from fastapi import APIRouter
from app.api.rest import router as rest_router
from app.api.websocket import router as websocket_router

api_router = APIRouter()
api_router.include_router(rest_router)
api_router.include_router(websocket_router)
