from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router
from app.core.config import Settings
from app.core.registry import AppRegistry


def test_dashboard_management(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(
        api_key="x" * 40, admin_key="y" * 40, device="cpu", compute="int8"
    )
    registry = AppRegistry(str(tmp_path / "registry.db"))
    registry.initialize()
    app.state.registry = registry
    app.state.app_slots = SimpleNamespace(counts={})
    admin = {"Authorization": "Bearer " + "y" * 40}
    with TestClient(app) as client:
        shell = client.get("/admin")
        assert shell.status_code == 200
        assert "Content-Security-Policy" in shell.headers
        assert "y" * 40 not in shell.text
        assert client.get("/v1/admin/apps").status_code == 401
        assert client.post("/v1/admin/apps", json={"name": "Ritmo"}).status_code == 401
        created = client.post("/v1/admin/apps", headers=admin, json={"name": "Ritmo"})
        assert created.status_code == 201
        identity = created.json()["id"]
        key = created.json()["api_key"]
        assert created.headers["Cache-Control"] == "no-store"
        assert registry.identify(key) == identity
        path = "/v1/admin/apps/" + identity
        token, _ = registry.issue_token(key)
        for credential in (key, token, "x" * 40):
            headers = {"Authorization": "Bearer " + credential}
            assert client.get("/v1/admin/apps", headers=headers).status_code == 401
            for action in ("rotate", "revoke", "revoke-tokens"):
                assert client.post(path + "/" + action, headers=headers).status_code == 401
        listing = client.get("/v1/admin/apps", headers=admin)
        assert listing.json()["data"][0]["status"] == "active"
        assert key not in listing.text
        rotated = client.post(path + "/rotate", headers=admin)
        assert rotated.status_code == 200
        new_key = rotated.json()["api_key"]
        assert registry.identify(key) is None
        assert registry.identify(token) is None
        assert registry.identify(new_key) == identity
        assert client.post(path + "/revoke", headers=admin).status_code == 200
        assert registry.identify(new_key) is None
        assert client.post(path + "/rotate", headers=admin).status_code == 409
        assert client.get("/v1/admin/apps", headers=admin).json()["data"][0]["status"] == "revoked"
        assert client.post("/v1/admin/apps/missing/revoke", headers=admin).status_code == 404


def test_disabled_dashboard(tmp_path):
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(api_key="x" * 40, admin_key=None, device="cpu", compute="int8")
    with TestClient(app) as client:
        assert client.get("/admin").status_code == 404
        assert client.get("/v1/admin/apps").status_code == 404
