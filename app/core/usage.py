"""Atomic UTC usage accounting, shared by all credentials of an application."""
from contextlib import closing
from datetime import datetime, timezone

from app.core.registry import AppRegistry


class UsageLimit(Exception):
    pass


class UsageStore:
    def __init__(self, registry: AppRegistry):
        self.registry = registry

    def initialize(self) -> None:
        with closing(self.registry.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS usage_buckets (
                app_id TEXT NOT NULL, period TEXT NOT NULL, bucket TEXT NOT NULL,
                amount INTEGER NOT NULL, PRIMARY KEY(app_id, period, bucket)
            )""")

    def charge(
        self, app_id: str, requests: int, samples: int,
        rpm: int, daily_samples: int, monthly_samples: int,
        now: datetime | None = None,
    ) -> None:
        if requests < 0 or samples < 0:
            raise ValueError("Negative usage")
        now = now or datetime.now(timezone.utc)
        buckets = (
            ("minute", now.strftime("%Y-%m-%dT%H:%M"), requests, rpm),
            ("day", now.strftime("%Y-%m-%d"), samples, daily_samples),
            ("month", now.strftime("%Y-%m"), samples, monthly_samples),
        )
        with closing(self.registry.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for period, bucket, amount, limit in buckets:
                if not amount:
                    continue
                row = db.execute(
                    "SELECT amount FROM usage_buckets WHERE app_id=? AND period=? AND bucket=?",
                    (app_id, period, bucket),
                ).fetchone()
                if (row[0] if row else 0) + amount > limit:
                    raise UsageLimit("Application usage limit exceeded")
                db.execute(
                    """INSERT INTO usage_buckets VALUES (?,?,?,?)
                    ON CONFLICT(app_id,period,bucket) DO UPDATE SET amount=amount+excluded.amount""",
                    (app_id, period, bucket, amount),
                )


class AppSlots:
    """Single-process combined REST/WS admission; no await between check and increment."""
    def __init__(self):
        self.counts: dict[str, int] = {}

    def acquire(self, app_id: str, limit: int) -> None:
        count = self.counts.get(app_id, 0)
        if count >= limit:
            raise UsageLimit("Application concurrency exceeded")
        self.counts[app_id] = count + 1

    def release(self, app_id: str) -> None:
        count = self.counts[app_id] - 1
        if count:
            self.counts[app_id] = count
        else:
            del self.counts[app_id]
