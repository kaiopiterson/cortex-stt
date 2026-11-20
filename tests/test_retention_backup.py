"""AUD-14: retention and SQLite backup/restore."""

import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.core.maintenance import RetentionSchedule, run_retention
from app.core.registry import AppRegistry
from app.core.usage import UsageStore
from app.db_backup import backup, restore

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def setup(tmp_path):
    registry = AppRegistry(str(tmp_path / "registry.db"))
    registry.initialize()
    usage = UsageStore(registry)
    usage.initialize()
    return registry, usage


def test_retention_removes_only_expired_data(tmp_path):
    registry, usage = setup(tmp_path)
    settings = Settings(api_key="x" * 40, device="cpu", compute="int8")
    for when in (
        NOW - timedelta(days=3),
        NOW - timedelta(minutes=5),
        NOW - timedelta(days=401),
        NOW - timedelta(days=10),
    ):
        usage.charge("app", 1, 1, 10**6, 10**9, 10**9, now=when)
    app_id, key = registry.register("Ritmo")
    registry.issue_token(key, ttl=1)
    live, _ = registry.issue_token(key, ttl=3600)
    registry.record_audit("register", app_id, "cli")
    with sqlite3.connect(registry.path) as db:
        db.execute(
            "UPDATE admin_audit SET created_at=?", (int((NOW - timedelta(days=500)).timestamp()),)
        )
    registry.record_audit("rotate", app_id, "cli")
    removed = run_retention(
        settings, registry, usage, now=datetime.fromtimestamp(time.time() + 5, timezone.utc)
    )
    assert removed["expired_tokens"] == 1
    assert removed["audit_events"] == 1
    assert registry.identify(live) == app_id
    removed = run_retention(settings, registry, usage, now=NOW)
    with sqlite3.connect(registry.path) as db:
        minutes = [
            row[0]
            for row in db.execute(
                "SELECT bucket FROM usage_buckets WHERE period='minute' ORDER BY bucket"
            )
        ]
        days = [
            row[0]
            for row in db.execute(
                "SELECT bucket FROM usage_buckets WHERE period='day' ORDER BY bucket"
            )
        ]
    assert minutes == [(NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")]
    assert (NOW - timedelta(days=401)).strftime("%Y-%m-%d") not in days
    assert (NOW - timedelta(days=10)).strftime("%Y-%m-%d") in days


def test_retention_schedule():
    schedule = RetentionSchedule(3600)
    assert schedule.due()
    schedule.mark()
    assert not schedule.due()


def test_backup_and_restore_round_trip(tmp_path):
    registry, _ = setup(tmp_path)
    app_id, key = registry.register("Ritmo")
    result = backup(registry.path, str(tmp_path / "backups" / "copy.sqlite3"))
    assert result["integrity"] == "ok"
    with pytest.raises(FileExistsError):
        backup(registry.path, result["backup"])
    registry.revoke_app(app_id)
    registry.register("Created after backup")
    restore(result["backup"], registry.path)
    restored = AppRegistry(registry.path)
    assert [app["name"] for app in restored.list_apps()] == ["Ritmo"]
    assert restored.identify(key) == app_id


def test_restore_rejects_corrupted_backup(tmp_path):
    registry, _ = setup(tmp_path)
    broken = tmp_path / "broken.sqlite3"
    broken.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        restore(str(broken), registry.path)
