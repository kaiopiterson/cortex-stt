from datetime import datetime, timezone

from app.core.registry import AppRegistry
from app.core.usage import UsageStore


def test_report_persistence(tmp_path):
    registry = AppRegistry(str(tmp_path / "registry.db"))
    registry.initialize()
    identity, key = registry.register("Ritmo")
    store = UsageStore(registry)
    store.initialize()
    store.charge(identity, 1, 16000, 60, 32000, 64000,
                 datetime(2026, 1, 1, tzinfo=timezone.utc))
    rows = UsageStore(registry).report()
    assert len(rows) == 3
    assert {row["period"] for row in rows} == {"minute", "day", "month"}
    assert key not in str(rows)
    assert len(store.report(1)) == 1
