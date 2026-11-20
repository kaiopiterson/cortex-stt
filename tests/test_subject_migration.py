import sqlite3

import pytest

from app.core.registry import AppRegistry


def test_subject_migration_preserves_existing_application(tmp_path):
    path = tmp_path / "registry.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE applications (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
            "key_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        db.execute("INSERT INTO applications(id,name,key_hash) VALUES ('old','Old','hash')")
    registry = AppRegistry(str(path))
    registry.initialize()
    registry.initialize()
    assert registry.list_apps()[0]["id"] == "old"
    app_id, key = registry.register("Ritmo")
    issued = registry.issue_token(key, subject="device-1")
    assert issued is not None
    token, _ = issued
    assert registry.identify(token) == app_id
    assert registry.token_subject(token) == "device-1"
    with pytest.raises(ValueError):
        registry.issue_token(key, subject="invalid subject")
