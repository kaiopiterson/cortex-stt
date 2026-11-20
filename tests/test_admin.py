from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.core.config import Settings


def test_admin_isolation():
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(api_key="x"*40, device="cpu", compute="int8")
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 404
        app.state.settings = Settings(
            api_key="x"*40, admin_key="y"*40, device="cpu", compute="int8"
        )
        app.state.inference_service = Mock()
        app.state.inference_service.metrics_snapshot.return_value = {}
        app.state.app_slots = SimpleNamespace(counts={})
        app.state.usage = Mock()
        app.state.usage.report.return_value = []
        for path in ("/metrics", "/v1/admin/usage"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer " + "x"*40}).status_code == 401
            response = client.get(path, headers={"Authorization": "Bearer " + "y"*40})
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
