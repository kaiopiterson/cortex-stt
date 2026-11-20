"""Gateway admission for CortexLab STT v0.3.0.

Single entry point for quotas, rate limits, concurrency and queue priority.
Every authenticated identity is metered at two levels:

* application scope (``app_id``), with limits stored per application in the
  registry and falling back to the ``STT_APP_*`` defaults;
* subject scope (``app_id#subject``), when the access token carries a subject,
  with the ``STT_SUBJECT_*`` limits.

The legacy credential is the internal application ``legacy``.
"""

import asyncio
import sqlite3
import time
from dataclasses import dataclass

from starlette.requests import HTTPConnection

from app.core.auth import CredentialStoreUnavailable, Identity
from app.core.registry import PRIORITY_CLASSES, AppRegistry
from app.core.usage import AppSlots, Reservation, Scope, UsageLedger, scope_key

PRIORITY_RANK = {name: rank for rank, name in enumerate(PRIORITY_CLASSES)}
LEGACY_APP_ID = "legacy"


@dataclass(frozen=True)
class Policy:
    requests_per_minute: int
    daily_audio_seconds: int
    monthly_audio_seconds: int
    concurrency: int
    priority_class: str = "normal"


@dataclass
class Admission:
    """Concurrency slots and usage held by one REST request or WS session."""

    identity: Identity
    scopes: list[Scope]
    priority_class: str
    slot_keys: list[str]
    reservations: list[Reservation]

    def priority(self, interactive: bool) -> int:
        """Queue priority: lower runs first. Class dominates, then mode."""
        return PRIORITY_RANK.get(self.priority_class, 1) * 2 + (0 if interactive else 1)


class Gateway:
    def __init__(
        self,
        settings,
        registry: AppRegistry | None,
        ledger: UsageLedger | None,
        slots: AppSlots,
        policy_ttl: float = 5.0,
    ):
        self.settings = settings
        self.registry = registry
        self.ledger = ledger
        self.slots = slots
        self.policy_ttl = policy_ttl
        self._policies: dict[str, tuple[float, Policy]] = {}

    def default_policy(self) -> Policy:
        s = self.settings
        return Policy(
            s.app_requests_per_minute,
            s.app_daily_audio_seconds,
            s.app_monthly_audio_seconds,
            s.app_concurrency,
        )

    def invalidate(self, app_id: str | None = None) -> None:
        if app_id is None:
            self._policies.clear()
        else:
            self._policies.pop(app_id, None)

    async def policy(self, app_id: str) -> Policy:
        cached = self._policies.get(app_id)
        if cached and time.monotonic() - cached[0] < self.policy_ttl:
            return cached[1]
        defaults = self.default_policy()
        overrides = None
        if self.registry is not None and app_id != LEGACY_APP_ID:
            try:
                overrides = await asyncio.to_thread(self.registry.policy_overrides, app_id)
            except sqlite3.Error as exc:
                raise CredentialStoreUnavailable("Policy store unavailable") from exc
        policy = defaults
        if overrides is not None:
            policy = Policy(
                overrides.requests_per_minute or defaults.requests_per_minute,
                overrides.daily_audio_seconds or defaults.daily_audio_seconds,
                overrides.monthly_audio_seconds or defaults.monthly_audio_seconds,
                overrides.concurrency or defaults.concurrency,
                overrides.priority_class,
            )
        self._policies[app_id] = (time.monotonic(), policy)
        return policy

    def scopes(self, identity: Identity, policy: Policy) -> list[Scope]:
        rate = self.settings.sample_rate
        result = [
            Scope(
                scope_key(identity.app_id),
                policy.requests_per_minute,
                policy.daily_audio_seconds * rate,
                policy.monthly_audio_seconds * rate,
                policy.concurrency,
            )
        ]
        if identity.subject:
            s = self.settings
            result.append(
                Scope(
                    scope_key(identity.app_id, identity.subject),
                    s.subject_requests_per_minute,
                    s.subject_daily_audio_seconds * rate,
                    s.subject_monthly_audio_seconds * rate,
                    s.subject_concurrency,
                )
            )
        return result

    async def admit(self, identity: Identity, *, hold_slot: bool) -> Admission:
        """Charge one request and optionally take a concurrency slot per scope."""
        policy = await self.policy(identity.app_id)
        scopes = self.scopes(identity, policy)
        reservations: list[Reservation] = []
        if self.ledger is not None:
            reservations.append(await self.ledger.reserve(scopes, requests=1))
        slot_keys: list[str] = []
        if hold_slot:
            slot_keys = self.slots.acquire_many(
                [(scope.key, scope.concurrency) for scope in scopes]
            )
        return Admission(identity, scopes, policy.priority_class, slot_keys, reservations)

    def hold(self, admission: Admission) -> None:
        """Take one concurrency slot per scope for an already admitted request."""
        if not admission.slot_keys:
            admission.slot_keys = self.slots.acquire_many(
                [(scope.key, scope.concurrency) for scope in admission.scopes]
            )

    async def charge_samples(self, admission: Admission, samples: int) -> Reservation | None:
        if self.ledger is None or samples <= 0:
            return None
        reservation = await self.ledger.reserve(admission.scopes, samples=samples)
        admission.reservations.append(reservation)
        return reservation

    def credit_samples(self, admission: Admission | None, samples: int) -> None:
        if self.ledger is not None and admission is not None:
            self.ledger.credit_samples(admission.scopes, samples)

    def refund(self, reservation: Reservation | None) -> None:
        if self.ledger is not None:
            self.ledger.refund(reservation)

    def release(self, admission: Admission | None) -> None:
        if admission is not None and admission.slot_keys:
            self.slots.release_many(admission.slot_keys)
            admission.slot_keys = []


def gateway(connection: HTTPConnection) -> Gateway:
    """Return the app gateway, creating one lazily for tests and tools."""
    state = connection.app.state
    current = getattr(state, "gateway", None)
    if current is None:
        slots = getattr(state, "app_slots", None)
        if slots is None:
            slots = AppSlots()
            state.app_slots = slots
        ledger = getattr(state, "ledger", None)
        if ledger is None and getattr(state, "usage", None) is not None:
            ledger = UsageLedger(state.usage)
            state.ledger = ledger
        current = Gateway(state.settings, getattr(state, "registry", None), ledger, slots)
        state.gateway = current
    return current
