from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.tokens import router
from app.core.config import Settings
from app.core.limiter import limiter
from app.core.registry import AppRegistry


def test_exchange_http(tmp_path):
    app = FastAPI()
    app.state.settings = Settings(api_key="x" * 40, device="cpu", compute="int8")
    app.state.registry = AppRegistry(str(tmp_path / "registry.db"))
    app.state.registry.initialize()
    app_id, key = app.state.registry.register("Ritmo")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(router)
    limiter.reset()
    try:
        with TestClient(app) as client:
            response = client.post("/v1/auth/token", headers={"Authorization": f"Bearer {key}"})
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            token = response.json()["access_token"]
            assert app.state.registry.identify(token) == app_id
            for invalid in (token, "x" * 40, "invalid"):
                response = client.post(
                    "/v1/auth/token", headers={"Authorization": f"Bearer {invalid}"}
                )
                assert response.status_code == 401
            for _ in range(26):
                assert client.post(
                    "/v1/auth/token", headers={"Authorization": f"Bearer {key}"}
                ).status_code == 200
            assert client.post(
                "/v1/auth/token", headers={"Authorization": f"Bearer {key}"}
            ).status_code == 429
    finally:
        limiter.reset()
