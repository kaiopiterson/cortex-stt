"""Persistent application registry. Secrets are returned only on creation."""
import hashlib
import secrets
import sqlite3
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

    def identify(self, key: str) -> str | None:
        if not key.startswith("stt_") or len(key) != 47:
            return None
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT id FROM applications WHERE key_hash=?", (self.digest(key),)
            ).fetchone()
        return str(row[0]) if row else None

    def list_apps(self) -> list[dict[str, str]]:
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT id,name,created_at FROM applications ORDER BY created_at,id"
            ).fetchall()
        return [{"id": row[0], "name": row[1], "created_at": row[2]} for row in rows]
