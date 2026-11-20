from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import build_router
from app.core.body_limit import BodyLimit
from app.core.config import Settings


def test_admin_rejected_before_body():
    app = FastAPI()
    app.state.settings = Settings(api_key="L" * 40, device="cpu", compute="int8")
    app.add_middleware(BodyLimit)
    app.include_router(build_router())
    with TestClient(app) as client:
        assert client.post("/v1/admin/apps", content=b"{").status_code == 404
        assert client.get("/admin").status_code == 404
        assert client.post("/other", content=b"x" * 65537).status_code == 413


def test_admin_shell_is_served_but_api_requires_auth():
    """The dashboard HTML holds no secret; the browser cannot send a bearer on page load."""
    app = FastAPI()
    app.state.settings = Settings(
        api_key="L" * 40, admin_key="A" * 40, device="cpu", compute="int8"
    )
    app.add_middleware(BodyLimit)
    app.include_router(build_router(include_admin=True))
    with TestClient(app) as client:
        shell = client.get("/admin")
        assert shell.status_code == 200
        assert "A" * 40 not in shell.text
        assert client.post("/v1/admin/apps", content=b"{").status_code == 401
        assert client.get("/metrics").status_code == 401
