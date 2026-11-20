"""Usage accounting for CortexLab STT v0.3.0.

Consumption is tracked per scope: the application (``app_id``) and, when the
token carries a subject, the pair ``app_id#subject``. Limits are checked in
memory by :class:`UsageLedger`, which persists deltas to SQLite in batches so
the database stays off the streaming hot path. This design assumes a single
API process (one Uvicorn worker), as documented in docs/gateway.md.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.registry import AppRegistry

logger = logging.getLogger(__name__)

BucketKey = tuple[str, str, str]  # (scope, period, bucket)
SUBJECT_SEPARATOR = "#"


class UsageLimit(Exception):
    """A rate, quota or concurrency limit was reached."""

    def __init__(self, message: str = "Application usage limit exceeded", *, scope: str = ""):
        super().__init__(message)
        self.scope = scope


@dataclass(frozen=True)
class Scope:
    """A metered identity with its limits (samples for audio, requests per minute)."""

    key: str
    requests_per_minute: int
    daily_samples: int
    monthly_samples: int
    concurrency: int


def scope_key(app_id: str, subject: str | None = None) -> str:
    return app_id if subject is None else f"{app_id}{SUBJECT_SEPARATOR}{subject}"


def bucket_names(now: datetime) -> tuple[str, str, str]:
    return now.strftime("%Y-%m-%dT%H:%M"), now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")


class UsageStore:
    """SQLite persistence for usage buckets."""

    def __init__(self, registry: AppRegistry):
        self.registry = registry

    def initialize(self) -> None:
        with closing(self.registry.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS usage_buckets (
                app_id TEXT NOT NULL, period TEXT NOT NULL, bucket TEXT NOT NULL,
                amount INTEGER NOT NULL, PRIMARY KEY(app_id, period, bucket)
            )""")

    def load(self, keys: Iterable[BucketKey]) -> dict[BucketKey, int]:
        result: dict[BucketKey, int] = {}
        with closing(self.registry.connect()) as db:
            for key in keys:
                row = db.execute(
                    "SELECT amount FROM usage_buckets WHERE app_id=? AND period=? AND bucket=?",
                    key,
                ).fetchone()
                result[key] = int(row[0]) if row else 0
        return result

    def apply(self, deltas: dict[BucketKey, int]) -> None:
        """Persist accumulated deltas in a single transaction."""
        with closing(self.registry.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for (scope, period, bucket), amount in deltas.items():
                if amount == 0:
                    continue
                db.execute(
                    """INSERT INTO usage_buckets VALUES (?,?,?,MAX(?,0))
                    ON CONFLICT(app_id,period,bucket)
                    DO UPDATE SET amount=MAX(amount+excluded.amount, 0)""",
                    (scope, period, bucket, amount),
                )

    def report(self, limit: int = 100) -> list[dict]:
        with closing(self.registry.connect()) as db:
            rows = db.execute(
                """SELECT app_id,period,bucket,amount FROM usage_buckets
                ORDER BY bucket DESC,app_id,period LIMIT ?""",
                (limit,),
            ).fetchall()
        report = []
        for scope, period, bucket, amount in rows:
            app_id, _, subject = scope.partition(SUBJECT_SEPARATOR)
            report.append(
                {
                    "app_id": app_id,
                    "subject": subject or None,
                    "period": period,
                    "bucket": bucket,
                    "amount": amount,
                    "unit": "requests" if period == "minute" else "samples",
                }
            )
        return report

    def purge(self, now: datetime, minute_days: int, day_days: int) -> dict[str, int]:
        minute_cutoff = (now - timedelta(days=minute_days)).strftime("%Y-%m-%dT%H:%M")
        day_cutoff = (now - timedelta(days=day_days)).strftime("%Y-%m-%d")
        with closing(self.registry.connect()) as db, db:
            minutes = db.execute(
                "DELETE FROM usage_buckets WHERE period='minute' AND bucket<?", (minute_cutoff,)
            ).rowcount
            days = db.execute(
                "DELETE FROM usage_buckets WHERE period='day' AND bucket<?", (day_cutoff,)
            ).rowcount
        return {"minute_buckets": minutes, "day_buckets": days}

    def charge(
        self,
        app_id: str,
        requests: int,
        samples: int,
        rpm: int,
        daily_samples: int,
        monthly_samples: int,
        now: datetime | None = None,
    ) -> None:
        """Synchronous check-and-charge used by maintenance tools and tests."""
        if requests < 0 or samples < 0:
            raise ValueError("Negative usage")
        now = now or datetime.now(timezone.utc)
        minute, day, month = bucket_names(now)
        buckets = (
            ("minute", minute, requests, rpm),
            ("day", day, samples, daily_samples),
            ("month", month, samples, monthly_samples),
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
                    raise UsageLimit(scope=app_id)
                db.execute(
                    """INSERT INTO usage_buckets VALUES (?,?,?,?)
                    ON CONFLICT(app_id,period,bucket) DO UPDATE SET amount=amount+excluded.amount""",
                    (app_id, period, bucket, amount),
                )


@dataclass
class Reservation:
    """Amounts added by one charge, so they can be refunded on server failure."""

    entries: list[tuple[BucketKey, int]] = field(default_factory=list)


class UsageLedger:
    """In-memory, check-then-apply usage ledger with batched persistence.

    All checks and increments happen on the event loop thread without an
    ``await`` in between, so they are atomic for a single process. Totals for a
    bucket are loaded from SQLite the first time the bucket is touched.
    """

    def __init__(self, store: UsageStore):
        self.store = store
        self.totals: dict[BucketKey, int] = {}
        self.pending: dict[BucketKey, int] = {}
        self._flush_lock = asyncio.Lock()

    async def _ensure_loaded(self, keys: list[BucketKey]) -> None:
        missing = [key for key in keys if key not in self.totals]
        if not missing:
            return
        loaded = await asyncio.to_thread(self.store.load, missing)
        for key, amount in loaded.items():
            self.totals.setdefault(key, amount)

    async def reserve(
        self,
        scopes: list[Scope],
        requests: int = 0,
        samples: int = 0,
        now: datetime | None = None,
    ) -> Reservation:
        if requests < 0 or samples < 0:
            raise ValueError("Negative usage")
        now = now or datetime.now(timezone.utc)
        minute, day, month = bucket_names(now)
        plan: list[tuple[BucketKey, int, int, str]] = []
        for scope in scopes:
            if requests:
                plan.append(
                    ((scope.key, "minute", minute), requests, scope.requests_per_minute, scope.key)
                )
            if samples:
                plan.append(((scope.key, "day", day), samples, scope.daily_samples, scope.key))
                plan.append(
                    ((scope.key, "month", month), samples, scope.monthly_samples, scope.key)
                )
        await self._ensure_loaded([key for key, _, _, _ in plan])
        for key, amount, limit, owner in plan:
            if self.totals[key] + amount > limit:
                raise UsageLimit(scope=owner)
        reservation = Reservation()
        for key, amount, _, _ in plan:
            self.totals[key] += amount
            self.pending[key] = self.pending.get(key, 0) + amount
            reservation.entries.append((key, amount))
        return reservation

    def refund(self, reservation: Reservation | None) -> None:
        if reservation is None:
            return
        for key, amount in reservation.entries:
            self.totals[key] = max(0, self.totals.get(key, 0) - amount)
            self.pending[key] = self.pending.get(key, 0) - amount
        reservation.entries.clear()

    def credit_samples(
        self, scopes: list[Scope], samples: int, now: datetime | None = None
    ) -> None:
        """Return audio that was charged but never processed (server-side failure)."""
        if samples <= 0:
            return
        now = now or datetime.now(timezone.utc)
        _, day, month = bucket_names(now)
        for scope in scopes:
            for key in ((scope.key, "day", day), (scope.key, "month", month)):
                if key in self.totals:
                    credited = min(samples, self.totals[key])
                    self.totals[key] -= credited
                    self.pending[key] = self.pending.get(key, 0) - credited

    async def flush(self) -> bool:
        """Persist pending deltas. Returns False when the database was unavailable."""
        async with self._flush_lock:
            if not self.pending:
                return True
            batch, self.pending = self.pending, {}
            try:
                await asyncio.to_thread(self.store.apply, batch)
            except sqlite3.Error:
                logger.warning("Usage flush deferred: database unavailable")
                for key, amount in batch.items():
                    self.pending[key] = self.pending.get(key, 0) + amount
                return False
            return True

    def forget_stale(self, now: datetime | None = None) -> None:
        """Drop cached totals for closed buckets that have nothing pending."""
        now = now or datetime.now(timezone.utc)
        current = set(bucket_names(now))
        for key in [k for k in self.totals if k[2] not in current and not self.pending.get(k)]:
            del self.totals[key]


class AppSlots:
    """Single-process concurrency admission across REST and WebSocket.

    ``acquire_many`` checks every scope first and only then increments, so a
    rejected request never holds a partial slot.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def acquire_many(self, limits: list[tuple[str, int]]) -> list[str]:
        for key, limit in limits:
            if self.counts.get(key, 0) >= limit:
                raise UsageLimit("Application concurrency exceeded", scope=key)
        for key, _ in limits:
            self.counts[key] = self.counts.get(key, 0) + 1
        return [key for key, _ in limits]

    def release_many(self, keys: list[str]) -> None:
        for key in keys:
            self.release(key)

    def acquire(self, app_id: str, limit: int) -> None:
        self.acquire_many([(app_id, limit)])

    def release(self, app_id: str) -> None:
        count = self.counts.get(app_id, 0) - 1
        if count > 0:
            self.counts[app_id] = count
        else:
            self.counts.pop(app_id, None)
