"""Retention for the gateway database (CortexLab STT v0.3.0, AUD-14)."""

import logging
import time
from datetime import datetime, timezone

from app.core.registry import AppRegistry
from app.core.usage import UsageStore

logger = logging.getLogger(__name__)


def run_retention(
    settings, registry: AppRegistry, usage: UsageStore, now: datetime | None = None
) -> dict[str, int]:
    """Delete data past its retention window. Safe to run while serving."""
    now = now or datetime.now(timezone.utc)
    removed = usage.purge(now, settings.retention_minute_days, settings.retention_day_days)
    removed.update(registry.purge(int(now.timestamp()), settings.retention_audit_days * 86400))
    if any(removed.values()):
        logger.info("Retention removed %s", removed)
    return removed


class RetentionSchedule:
    """Decides when the next retention pass is due."""

    def __init__(self, interval_seconds: int):
        self.interval = interval_seconds
        self.last_run: float | None = None

    def due(self) -> bool:
        return self.last_run is None or time.monotonic() - self.last_run >= self.interval

    def mark(self) -> None:
        self.last_run = time.monotonic()
