import sqlite3

import pytest

from app.core.registry import AppRegistry


def test_persistence_and_secret_storage(tmp_path):
    path = str(tmp_path / "registry.sqlite3")
    registry = AppRegistry(path)
    registry.initialize()
    first, key = registry.register("Ritmo")
    second, other = registry.register("Voice Studio")
    assert first != second
    assert key != other
    reopened = AppRegistry(path)
    reopened.initialize()
    assert reopened.identify(key) == first
    assert reopened.identify(other) == second
    assert reopened.identify("stt_" + "x" * 43) is None
    assert all("api_key" not in row for row in reopened.list_apps())
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        stored = db.execute("SELECT key_hash FROM applications").fetchall()
    assert (key,) not in stored
    assert (registry.digest(key),) in stored


@pytest.mark.parametrize("name", ["", " ", "x" * 101])
def test_invalid_name(tmp_path, name):
    registry = AppRegistry(str(tmp_path / "registry.db"))
    registry.initialize()
    with pytest.raises(ValueError):
        registry.register(name)
