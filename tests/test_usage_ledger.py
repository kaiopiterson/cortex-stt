"""AUD-12 and AUD-18: batched usage persistence, identity cache and refunds."""

import asyncio
import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.core.registry import AppRegistry
from app.core.usage import Scope, UsageLedger, UsageLimit, UsageStore
from app.services.inference import InferenceBusyError
from test_audit_regressions import build

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def make_store(tmp_path):
    registry = AppRegistry(str(tmp_path / "usage.db"))
    registry.initialize()
    store = UsageStore(registry)
    store.initialize()
    return store


def scope(key="app", daily=10**9):
    return Scope(key, 10**6, daily, 10**9, 10)


async def test_many_sessions_flush_in_one_batch(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    ledger = UsageLedger(store)
    writes = []
    original = store.apply
    monkeypatch.setattr(store, "apply", lambda batch: (writes.append(batch), original(batch)))
    scopes = [scope(f"app#s{i}") for i in range(10)]
    for _ in range(50):
        for item in scopes:
            await ledger.reserve([item], samples=4096, now=NOW)
    assert writes == []  # nothing touches SQLite on the hot path
    assert await ledger.flush()
    assert len(writes) == 1
    loaded = store.load([("app#s3", "day", "2026-11-20")])
    assert loaded[("app#s3", "day", "2026-11-20")] == 50 * 4096


async def test_totals_survive_restart_and_limits_hold(tmp_path):
    store = make_store(tmp_path)
    first = UsageLedger(store)
    await first.reserve([scope(daily=100)], samples=80, now=NOW)
    await first.flush()
    restarted = UsageLedger(store)
    with pytest.raises(UsageLimit):
        await restarted.reserve([scope(daily=100)], samples=30, now=NOW)
    await restarted.reserve([scope(daily=100)], samples=20, now=NOW)


async def test_flush_retries_when_database_is_unavailable(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    ledger = UsageLedger(store)
    await ledger.reserve([scope()], samples=10, now=NOW)

    def broken(_batch):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "apply", broken)
    assert not await ledger.flush()
    assert ledger.pending
    monkeypatch.undo()
    assert await ledger.flush()
    assert store.load([("app", "day", "2026-11-20")])[("app", "day", "2026-11-20")] == 10


async def test_refund_and_credit_restore_quota(tmp_path):
    store = make_store(tmp_path)
    ledger = UsageLedger(store)
    reservation = await ledger.reserve([scope(daily=100)], samples=100, now=NOW)
    ledger.refund(reservation)
    await ledger.reserve([scope(daily=100)], samples=100, now=NOW)
    ledger.credit_samples([scope(daily=100)], 40, now=NOW)
    await ledger.reserve([scope(daily=100)], samples=40, now=NOW)
    await ledger.flush()
    assert store.load([("app", "day", "2026-11-20")])[("app", "day", "2026-11-20")] == 100


async def test_concurrent_reservations_never_exceed_limit(tmp_path):
    ledger = UsageLedger(make_store(tmp_path))
    results = await asyncio.gather(
        *(ledger.reserve([scope(daily=1000)], samples=100, now=NOW) for _ in range(20)),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in results) == 10


def test_identity_lookups_are_cached(tmp_path, monkeypatch):
    app, registry = build(tmp_path)
    _, key = registry.register("Ritmo")
    token, _ = registry.issue_token(key)
    calls = []
    original = registry.lookup
    monkeypatch.setattr(registry, "lookup", lambda k: (calls.append(k), original(k))[1])
    with TestClient(app) as client:
        for _ in range(5):
            response = client.post(
                "/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": ("a.wav", b"RIFF....", "audio/wav")},
            )
            assert response.status_code == 200
    assert len(calls) == 1


def test_credential_store_outage_returns_503(tmp_path, monkeypatch):
    app, registry = build(tmp_path)
    _, key = registry.register("Ritmo")
    token, _ = registry.issue_token(key)

    def broken(_key):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(registry, "lookup", broken)
    with TestClient(app) as client:
        response = client.post(
            "/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("a.wav", b"RIFF....", "audio/wav")},
        )
    assert response.status_code == 503


def test_server_failure_refunds_audio(tmp_path):
    app, registry = build(tmp_path, subject_daily_audio_seconds=1)
    _, key = registry.register("Ritmo")
    token, _ = registry.issue_token(key, subject="alice")
    app.state.inference_service.transcribe = AsyncMock(side_effect=InferenceBusyError("busy"))
    headers = {"Authorization": f"Bearer {token}"}
    upload = {"file": ("a.wav", b"RIFF....", "audio/wav")}
    with TestClient(app) as client:
        # 0.5 s of audio per request against a 1 s subject quota: failures must not consume it.
        for _ in range(4):
            assert (
                client.post("/v1/audio/transcriptions", headers=headers, files=upload).status_code
                == 503
            )
    ledger = app.state.ledger
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert ledger.totals.get((f"{registry.lookup(token).app_id}#alice", "day", day), 0) == 0
