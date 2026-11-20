from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.registry import AppRegistry
from app.core.usage import AppSlots, UsageLimit, UsageStore


def test_atomic_shared_quota(tmp_path):
    registry = AppRegistry(str(tmp_path / "registry.db"))
    registry.initialize()
    identity, key = registry.register("Ritmo")
    token, _ = registry.issue_token(key)
    store = UsageStore(registry)
    store.initialize()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    store.charge(identity, 1, 60, 2, 100, 200, now)
    with pytest.raises(UsageLimit):
        store.charge(registry.identify(token), 1, 41, 2, 100, 200, now)
    reopened = UsageStore(registry)
    reopened.charge(identity, 1, 40, 2, 100, 200, now)
    with pytest.raises(UsageLimit):
        reopened.charge(identity, 1, 0, 2, 100, 200, now)


def test_concurrency():
    slots = AppSlots()
    slots.acquire("a", 1)
    with pytest.raises(UsageLimit):
        slots.acquire("a", 1)
    slots.acquire("b", 1)
    slots.release("a")
    slots.acquire("a", 1)


def test_parallel_charges(tmp_path):
    registry = AppRegistry(str(tmp_path / "registry.db"))
    registry.initialize()
    store = UsageStore(registry)
    store.initialize()

    def attempt(_):
        try:
            store.charge("a", 1, 1, 3, 3, 3)
            return True
        except UsageLimit:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(attempt, range(10))) == 3
