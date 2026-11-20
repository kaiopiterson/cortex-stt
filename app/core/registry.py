"""Persistent application registry. Secrets are returned only on creation."""
import hashlib
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path


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

    @staticmethod
    def digest(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def identify(self, key: str, *, ignore_expiry: bool = False) -> str | None:
        if len(key) != 47 or not key.startswith(("stt_", "tok_")):
            return None
        with closing(self.connect()) as db:
            if key.startswith("tok_"):
                row = db.execute(
                    """SELECT app_id FROM access_tokens WHERE token_hash=? AND (expires_at>? OR ?)
                    AND app_id NOT IN (SELECT app_id FROM revoked_apps)""",
                    (self.digest(key), int(time.time()), ignore_expiry),
                ).fetchone()
            else:
                row = db.execute(
                    """SELECT id FROM applications WHERE key_hash=?
                    AND id NOT IN (SELECT app_id FROM revoked_apps)""",
                    (self.digest(key),),
                ).fetchone()
        return str(row[0]) if row else None

    def list_apps(self) -> list[dict[str, str]]:
        with closing(self.connect()) as db:
            rows = db.execute(
                """SELECT id,name,created_at, CASE WHEN id IN (SELECT app_id FROM revoked_apps) THEN 'revoked' ELSE 'active' END FROM applications ORDER BY created_at,id"""
            ).fetchall()
        return [{"id": row[0], "name": row[1], "created_at": row[2], "status": row[3]} for row in rows]

    def issue_token(self, key: str, ttl: int = 900) -> tuple[str, int] | None:
        if not key.startswith("stt_") or len(key) != 47 or not 1 <= ttl <= 3600:
            return None
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
                "INSERT INTO access_tokens VALUES (?,?,?)",
                (self.digest(token), row[0], now + ttl),
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
