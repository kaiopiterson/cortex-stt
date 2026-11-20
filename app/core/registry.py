"""Persistent application registry for CortexLab STT v0.3.0.

Secrets are stored only as SHA-256 digests and returned once, on creation.
Schema changes are applied by numbered migrations recorded in schema_version.
"""

import hashlib
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

SUBJECT_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,64}")
PRIORITY_CLASSES = ("interactive", "normal", "batch")
LIMIT_COLUMNS = (
    "requests_per_minute",
    "daily_audio_seconds",
    "monthly_audio_seconds",
    "concurrency",
)


@dataclass(frozen=True)
class CredentialRecord:
    """Result of a credential lookup; expiry is evaluated by the caller."""

    app_id: str
    kind: str  # "permanent" | "token"
    subject: str | None
    expires_at: int | None


@dataclass(frozen=True)
class AppPolicyOverrides:
    """Per-application limits stored in the database (None = use defaults)."""

    requests_per_minute: int | None
    daily_audio_seconds: int | None
    monthly_audio_seconds: int | None
    concurrency: int | None
    priority_class: str


def _migration_1(db: sqlite3.Connection) -> None:
    for column in (
        "requests_per_minute INTEGER CHECK(requests_per_minute > 0)",
        "daily_audio_seconds INTEGER CHECK(daily_audio_seconds > 0)",
        "monthly_audio_seconds INTEGER CHECK(monthly_audio_seconds > 0)",
        "concurrency INTEGER CHECK(concurrency > 0)",
        "priority_class TEXT NOT NULL DEFAULT 'normal'",
    ):
        db.execute("ALTER TABLE applications ADD COLUMN " + column)
    db.execute("ALTER TABLE access_tokens ADD COLUMN subject TEXT")


def _migration_2(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS admin_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at INTEGER NOT NULL,
            action TEXT NOT NULL,
            app_id TEXT,
            actor TEXT NOT NULL,
            detail TEXT
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS admin_audit_created ON admin_audit(created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS tokens_expiry ON access_tokens(expires_at)")


MIGRATIONS: tuple[tuple[int, Callable[[sqlite3.Connection], None]], ...] = (
    (1, _migration_1),
    (2, _migration_2),
)


class AppRegistry:
    def __init__(self, path: str):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS applications (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS access_tokens (
                token_hash TEXT PRIMARY KEY,
                app_id TEXT NOT NULL REFERENCES applications(id),
                expires_at INTEGER NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS tokens_app ON access_tokens(app_id)")
            db.execute("""CREATE TABLE IF NOT EXISTS revoked_apps (
                app_id TEXT PRIMARY KEY REFERENCES applications(id)
            )""")
            db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
        for version, migrate in MIGRATIONS:
            with closing(self.connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                applied = db.execute(
                    "SELECT 1 FROM schema_version WHERE version=?", (version,)
                ).fetchone()
                if applied:
                    continue
                migrate(db)
                db.execute("INSERT INTO schema_version VALUES (?)", (version,))

    def schema_version(self) -> int:
        with closing(self.connect()) as db:
            row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0] or 0)

    @staticmethod
    def digest(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    @staticmethod
    def valid_subject(subject: str) -> bool:
        return SUBJECT_PATTERN.fullmatch(subject) is not None

    def register(self, name: str) -> tuple[str, str]:
        name = name.strip()
        if not 1 <= len(name) <= 100:
            raise ValueError("Invalid application name")
        app_id = secrets.token_hex(16)
        key = "stt_" + secrets.token_urlsafe(32)
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO applications(id,name,key_hash) VALUES (?,?,?)",
                (app_id, name, self.digest(key)),
            )
        return app_id, key

    def lookup(self, key: str) -> CredentialRecord | None:
        """Resolve a credential with a single query, ignoring token expiry."""
        if len(key) != 47 or not key.startswith(("stt_", "tok_")):
            return None
        with closing(self.connect()) as db:
            if key.startswith("tok_"):
                row = db.execute(
                    """SELECT app_id, subject, expires_at FROM access_tokens WHERE token_hash=?
                    AND app_id NOT IN (SELECT app_id FROM revoked_apps)""",
                    (self.digest(key),),
                ).fetchone()
                if not row:
                    return None
                return CredentialRecord(str(row[0]), "token", row[1], int(row[2]))
            row = db.execute(
                """SELECT id FROM applications WHERE key_hash=?
                AND id NOT IN (SELECT app_id FROM revoked_apps)""",
                (self.digest(key),),
            ).fetchone()
        return CredentialRecord(str(row[0]), "permanent", None, None) if row else None

    def identify(self, key: str, *, ignore_expiry: bool = False) -> str | None:
        record = self.lookup(key)
        if record is None:
            return None
        if (
            record.expires_at is not None
            and not ignore_expiry
            and record.expires_at <= int(time.time())
        ):
            return None
        return record.app_id

    def token_subject(self, key: str) -> str | None:
        record = self.lookup(key)
        return record.subject if record else None

    def list_apps(self) -> list[dict]:
        with closing(self.connect()) as db:
            rows = db.execute(
                """SELECT id, name, created_at,
                CASE WHEN id IN (SELECT app_id FROM revoked_apps) THEN 'revoked' ELSE 'active' END,
                requests_per_minute, daily_audio_seconds, monthly_audio_seconds, concurrency,
                priority_class
                FROM applications ORDER BY created_at, id"""
            ).fetchall()
        return [
            {
                "id": row[0],
                "name": row[1],
                "created_at": row[2],
                "status": row[3],
                "limits": {
                    "requests_per_minute": row[4],
                    "daily_audio_seconds": row[5],
                    "monthly_audio_seconds": row[6],
                    "concurrency": row[7],
                    "priority_class": row[8],
                },
            }
            for row in rows
        ]

    def exists(self, app_id: str) -> bool:
        with closing(self.connect()) as db:
            return (
                db.execute("SELECT 1 FROM applications WHERE id=?", (app_id,)).fetchone()
                is not None
            )

    def policy_overrides(self, app_id: str) -> AppPolicyOverrides | None:
        with closing(self.connect()) as db:
            row = db.execute(
                """SELECT requests_per_minute, daily_audio_seconds, monthly_audio_seconds,
                concurrency, priority_class FROM applications WHERE id=?""",
                (app_id,),
            ).fetchone()
        if not row:
            return None
        return AppPolicyOverrides(row[0], row[1], row[2], row[3], row[4] or "normal")

    def set_limits(self, app_id: str, **limits: int | str | None) -> None:
        """Update per-application limits. A None value restores the default."""
        assignments: list[str] = []
        values: list[int | str | None] = []
        for name, value in limits.items():
            if name in LIMIT_COLUMNS:
                if value is not None and (not isinstance(value, int) or value <= 0):
                    raise ValueError(f"Invalid {name}")
            elif name == "priority_class":
                if value not in PRIORITY_CLASSES:
                    raise ValueError("Invalid priority_class")
            else:
                raise ValueError(f"Unknown limit {name}")
            assignments.append(f"{name}=?")
            values.append(value)
        if not assignments:
            raise ValueError("No limits supplied")
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                f"UPDATE applications SET {', '.join(assignments)} WHERE id=?",
                (*values, app_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Unknown application")

    def issue_token(
        self, key: str, ttl: int = 900, subject: str | None = None
    ) -> tuple[str, int] | None:
        if not key.startswith("stt_") or len(key) != 47 or not 1 <= ttl <= 3600:
            return None
        if subject is not None and not self.valid_subject(subject):
            raise ValueError("Invalid subject")
        token = "tok_" + secrets.token_urlsafe(32)
        now = int(time.time())
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id FROM applications WHERE key_hash=?
                AND id NOT IN (SELECT app_id FROM revoked_apps)""",
                (self.digest(key),),
            ).fetchone()
            if not row:
                return None
            db.execute(
                "INSERT INTO access_tokens(token_hash,app_id,expires_at,subject) VALUES (?,?,?,?)",
                (self.digest(token), row[0], now + ttl, subject),
            )
        return token, ttl

    def rotate(self, app_id: str) -> str:
        key = "stt_" + secrets.token_urlsafe(32)
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                """UPDATE applications SET key_hash=? WHERE id=?
                AND id NOT IN (SELECT app_id FROM revoked_apps)""",
                (self.digest(key), app_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Unknown or revoked application")
            db.execute("DELETE FROM access_tokens WHERE app_id=?", (app_id,))
        return key

    def revoke_app(self, app_id: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO revoked_apps VALUES (?)", (app_id,))
            db.execute("DELETE FROM access_tokens WHERE app_id=?", (app_id,))

    def revoke_tokens(self, app_id: str) -> None:
        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM access_tokens WHERE app_id=?", (app_id,))

    def record_audit(
        self, action: str, app_id: str | None, actor: str, detail: str | None = None
    ) -> None:
        """Append an administrative event. Never pass secrets in detail."""
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO admin_audit(created_at, action, app_id, actor, detail) "
                "VALUES (?,?,?,?,?)",
                (int(time.time()), action, app_id, actor[:64], detail),
            )

    def audit_events(self, limit: int = 100) -> list[dict]:
        with closing(self.connect()) as db:
            rows = db.execute(
                """SELECT id, created_at, action, app_id, actor, detail FROM admin_audit
                ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "created_at": row[1],
                "action": row[2],
                "app_id": row[3],
                "actor": row[4],
                "detail": row[5],
            }
            for row in rows
        ]

    def purge(self, now: int, audit_retention_seconds: int) -> dict[str, int]:
        """Delete expired tokens and old audit events."""
        with closing(self.connect()) as db, db:
            tokens = db.execute("DELETE FROM access_tokens WHERE expires_at<=?", (now,)).rowcount
            audit = db.execute(
                "DELETE FROM admin_audit WHERE created_at<?", (now - audit_retention_seconds,)
            ).rowcount
        return {"expired_tokens": tokens, "audit_events": audit}
