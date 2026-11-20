"""AUD-10 and AUD-11: metrics token, admin network allowlist and audit trail."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from test_audit_regressions import build

ADMIN = "A" * 40
METRICS = "M" * 40


def headers(secret):
    return {"Authorization": f"Bearer {secret}"}


def test_metrics_token_reads_metrics_but_cannot_administer(tmp_path):
    app, _ = build(tmp_path, admin_key=ADMIN, metrics_token=METRICS)
    app.state.inference_service.metrics_snapshot = lambda: {}
    with TestClient(app) as client:
        assert client.get("/metrics", headers=headers(METRICS)).status_code == 200
        assert client.get("/metrics", headers=headers(ADMIN)).status_code == 200
        assert client.get("/v1/admin/apps", headers=headers(METRICS)).status_code == 401
        assert (
            client.post("/v1/admin/apps", headers=headers(METRICS), json={"name": "x"}).status_code
            == 401
        )
        assert client.get("/metrics", headers=headers("B" * 40)).status_code == 401


def test_metrics_available_with_only_metrics_token(tmp_path):
    from app.api import build_router
    from app.core.body_limit import BodyLimit
    from fastapi import FastAPI

    app, _ = build(tmp_path, metrics_token=METRICS)
    app.state.inference_service.metrics_snapshot = lambda: {}
    fresh = FastAPI()
    fresh.state.__dict__.update(app.state.__dict__)
    fresh.add_middleware(BodyLimit)
    fresh.include_router(build_router(include_admin=False, include_metrics=True))
    with TestClient(fresh) as client:
        assert client.get("/metrics", headers=headers(METRICS)).status_code == 200
        assert client.get("/v1/admin/apps", headers=headers(METRICS)).status_code == 404
        assert client.get("/admin").status_code == 404


def test_admin_hidden_outside_allowed_networks(tmp_path):
    app, _ = build(
        tmp_path,
        admin_key=ADMIN,
        metrics_token=METRICS,
        admin_allowed_networks=["10.0.0.0/8"],
        trust_proxy_headers=True,
    )
    app.state.inference_service.metrics_snapshot = lambda: {}
    outside = {"CF-Connecting-IP": "203.0.113.9"}
    inside = {"CF-Connecting-IP": "10.1.2.3"}
    with TestClient(app) as client:
        assert client.get("/admin", headers=outside).status_code == 404
        assert (
            client.get("/v1/admin/apps", headers={**outside, **headers(ADMIN)}).status_code == 404
        )
        assert client.get("/metrics", headers={**outside, **headers(METRICS)}).status_code == 404
        assert client.get("/admin", headers=inside).status_code == 200
        assert client.get("/v1/admin/apps", headers={**inside, **headers(ADMIN)}).status_code == 200


def test_admin_mutations_are_audited_without_secrets(tmp_path):
    app, registry = build(tmp_path, admin_key=ADMIN)
    with TestClient(app) as client:
        created = client.post("/v1/admin/apps", headers=headers(ADMIN), json={"name": "Ritmo"})
        app_id = created.json()["id"]
        key = created.json()["api_key"]
        rotated = client.post(f"/v1/admin/apps/{app_id}/rotate", headers=headers(ADMIN))
        client.put(
            f"/v1/admin/apps/{app_id}/limits", headers=headers(ADMIN), json={"concurrency": 2}
        )
        client.post(f"/v1/admin/apps/{app_id}/revoke-tokens", headers=headers(ADMIN))
        events = client.get("/v1/admin/audit", headers=headers(ADMIN)).json()["data"]
    assert [event["action"] for event in events] == [
        "revoke-tokens",
        "set-limits",
        "rotate",
        "register",
    ]
    assert all(event["app_id"] == app_id for event in events)
    assert all(event["actor"].startswith("admin@") for event in events)
    dump = repr(events)
    for secret in (key, rotated.json()["api_key"], ADMIN):
        assert secret not in dump


def test_metrics_token_must_be_independent():
    with pytest.raises(ValidationError):
        Settings(api_key="x" * 40, admin_key=ADMIN, metrics_token=ADMIN, device="cpu")
    with pytest.raises(ValidationError):
        Settings(api_key="x" * 40, metrics_token="short", device="cpu")
    with pytest.raises(ValidationError):
        Settings(api_key="x" * 40, admin_allowed_networks=["not-a-network"], device="cpu")
