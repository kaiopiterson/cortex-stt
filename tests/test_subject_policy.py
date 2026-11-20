"""AUD-07: per-application policy and per-subject isolation."""

import sqlite3
from contextlib import ExitStack
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.auth import Identity
from app.core.gateway import Gateway
from app.core.registry import AppRegistry
from app.core.usage import AppSlots, UsageLedger, UsageLimit, UsageStore
from app.registry_cli import main as cli_main
import pytest
from test_audit_regressions import build


def test_three_subjects_of_one_app_stream_concurrently(tmp_path):
    app, registry = build(tmp_path)
    _, key = registry.register("Ritmo")
    tokens = [registry.issue_token(key, subject=f"user-{i}")[0] for i in range(3)]
    with TestClient(app) as client:
        with ExitStack() as stack:
            opened = [
                stack.enter_context(client.websocket_connect("/v1/audio/stream")) for _ in tokens
            ]
            for ws, token in zip(opened, tokens):
                ws.send_json({"api_key": token, "mode": "utterance"})
                assert ws.receive_json()["type"] == "ready"
            assert app.state.app_slots.counts[registry.lookup(tokens[0]).app_id] == 3
        with client.websocket_connect("/v1/audio/stream") as again:
            again.send_json({"api_key": tokens[0], "mode": "utterance"})
            assert again.receive_json()["type"] == "ready"


def test_subject_concurrency_is_isolated(tmp_path):
    app, registry = build(tmp_path, subject_concurrency=1)
    _, key = registry.register("Ritmo")
    first, _ = registry.issue_token(key, subject="alice")
    second, _ = registry.issue_token(key, subject="alice")
    other, _ = registry.issue_token(key, subject="bob")
    with TestClient(app) as client:
        with client.websocket_connect("/v1/audio/stream") as ws:
            ws.send_json({"api_key": first, "mode": "utterance"})
            assert ws.receive_json()["type"] == "ready"
            with client.websocket_connect("/v1/audio/stream") as same:
                same.send_json({"api_key": second, "mode": "utterance"})
                assert same.receive_json()["code"] == "quota_exceeded"
            with client.websocket_connect("/v1/audio/stream") as bob:
                bob.send_json({"api_key": other, "mode": "utterance"})
                assert bob.receive_json()["type"] == "ready"


async def test_subject_quota_exhaustion_does_not_block_other_subjects(tmp_path):
    from app.core.config import Settings

    settings = Settings(
        api_key="x" * 40, device="cpu", compute="int8", subject_daily_audio_seconds=1
    )
    registry = AppRegistry(str(tmp_path / "r.db"))
    registry.initialize()
    store = UsageStore(registry)
    store.initialize()
    gate = Gateway(settings, registry, UsageLedger(store), AppSlots())
    app_id, _ = registry.register("Ritmo")
    alice = await gate.admit(Identity(app_id, "token", "alice"), hold_slot=False)
    bob = await gate.admit(Identity(app_id, "token", "bob"), hold_slot=False)
    await gate.charge_samples(alice, 16000)
    with pytest.raises(UsageLimit):
        await gate.charge_samples(alice, 1)
    await gate.charge_samples(bob, 16000)


async def test_per_app_limits_override_defaults(tmp_path):
    from app.core.config import Settings

    settings = Settings(api_key="x" * 40, device="cpu", compute="int8", app_concurrency=8)
    registry = AppRegistry(str(tmp_path / "r.db"))
    registry.initialize()
    app_id, _ = registry.register("Voice Studio")
    registry.set_limits(app_id, concurrency=1, priority_class="batch")
    gate = Gateway(settings, registry, None, AppSlots())
    first = await gate.admit(Identity(app_id, "token"), hold_slot=True)
    with pytest.raises(UsageLimit):
        await gate.admit(Identity(app_id, "token"), hold_slot=True)
    gate.release(first)
    again = await gate.admit(Identity(app_id, "token"), hold_slot=True)
    assert again.priority(interactive=True) > 2  # batch never jumps ahead of normal work
    registry.set_limits(app_id, concurrency=None)
    gate.invalidate(app_id)
    assert (await gate.policy(app_id)).concurrency == 8


async def test_priority_classes_order_the_queue(tmp_path):
    from app.core.config import Settings

    settings = Settings(api_key="x" * 40, device="cpu", compute="int8")
    registry = AppRegistry(str(tmp_path / "r.db"))
    registry.initialize()
    gate = Gateway(settings, registry, None, AppSlots())
    ranks = {}
    for name in ("interactive", "normal", "batch"):
        app_id, _ = registry.register(name)
        registry.set_limits(app_id, priority_class=name)
        admission = await gate.admit(Identity(app_id, "token"), hold_slot=False)
        ranks[name] = (admission.priority(True), admission.priority(False))
    assert ranks["interactive"] < ranks["normal"] < ranks["batch"]
    assert ranks["normal"][1] < ranks["batch"][0]


def test_token_endpoint_accepts_subject_and_rejects_invalid(tmp_path):
    app, registry = build(tmp_path)
    app_id, key = registry.register("Ritmo")
    with TestClient(app) as client:
        response = client.post(
            "/v1/auth/token",
            headers={"Authorization": f"Bearer {key}"},
            json={"subject": "device-42"},
        )
        assert response.status_code == 200
        record = registry.lookup(response.json()["access_token"])
        assert record is not None and record.subject == "device-42"
        bad = client.post(
            "/v1/auth/token",
            headers={"Authorization": f"Bearer {key}"},
            json={"subject": "has spaces"},
        )
        assert bad.status_code == 422


def test_admin_updates_limits_and_cli_sets_them(tmp_path, monkeypatch):
    app, registry = build(tmp_path, admin_key="A" * 40)
    app_id, _ = registry.register("Ritmo")
    headers = {"Authorization": "Bearer " + "A" * 40}
    with TestClient(app) as client:
        response = client.put(
            f"/v1/admin/apps/{app_id}/limits",
            headers=headers,
            json={"concurrency": 3, "priority_class": "interactive"},
        )
        assert response.status_code == 200
        assert (
            client.put(
                f"/v1/admin/apps/{app_id}/limits", headers=headers, json={"concurrency": 0}
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/v1/admin/apps/unknown/limits", headers=headers, json={"concurrency": 2}
            ).status_code
            == 404
        )
    overrides = registry.policy_overrides(app_id)
    assert overrides is not None and overrides.concurrency == 3
    assert overrides.priority_class == "interactive"

    monkeypatch.setattr("app.registry_cli.get_settings", lambda: _settings_for(registry))
    assert cli_main(["limits", app_id, "--rpm", "120", "--concurrency", "default"]) == 0
    overrides = registry.policy_overrides(app_id)
    assert overrides is not None
    assert overrides.requests_per_minute == 120 and overrides.concurrency is None


def _settings_for(registry):
    return SimpleNamespace(registry_path=registry.path)


def test_migrations_are_versioned_and_idempotent(tmp_path):
    path = tmp_path / "registry.db"
    registry = AppRegistry(str(path))
    registry.initialize()
    registry.initialize()
    assert registry.schema_version() == 2
    with sqlite3.connect(path) as db:
        versions = [row[0] for row in db.execute("SELECT version FROM schema_version")]
    assert versions == [1, 2]
