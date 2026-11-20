"""Install the same gateway state that app.main.lifespan creates."""

from pathlib import Path

from fastapi import FastAPI

from app.core.registry import AppRegistry
from app.core.usage import AppSlots, UsageStore


def install_gateway_state(app: FastAPI, directory: Path) -> AppRegistry:
    registry = AppRegistry(str(directory / "registry.sqlite3"))
    registry.initialize()
    usage = UsageStore(registry)
    usage.initialize()
    app.state.registry = registry
    app.state.usage = usage
    app.state.app_slots = AppSlots()
    return registry
