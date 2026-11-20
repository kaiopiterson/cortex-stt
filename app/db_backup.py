"""Consistent backup and restore of the gateway SQLite database (AUD-14).

Backup is safe while the service runs (SQLite online backup API, WAL included):
    docker compose exec stt python3 -m app.db_backup backup /data/backups/registry-$(date +%F).sqlite3

Restore only with the stt service stopped:
    docker compose stop stt
    docker compose run --rm --no-deps stt python3 -m app.db_backup restore /data/backups/FILE
    docker compose start stt
"""

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from app.core.config import get_settings


def _integrity(path: Path) -> str:
    with closing(sqlite3.connect(path)) as db:
        return str(db.execute("PRAGMA integrity_check").fetchone()[0])


def backup(source: str, destination: str) -> dict:
    target = Path(destination)
    if target.exists():
        raise FileExistsError(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
    status = _integrity(target)
    if status != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError(f"backup integrity check failed: {status}")
    return {"backup": str(target), "bytes": target.stat().st_size, "integrity": status}


def restore(backup_file: str, destination: str) -> dict:
    source = Path(backup_file)
    status = _integrity(source)
    if status != "ok":
        raise RuntimeError(f"refusing to restore a corrupted backup: {status}")
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)
        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {
        "restored": destination,
        "from": str(source),
        "integrity": _integrity(Path(destination)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backup").add_argument("destination")
    commands.add_parser("restore").add_argument("backup_file")
    args = parser.parse_args(argv)
    database = get_settings().registry_path
    try:
        if args.command == "backup":
            result = backup(database, args.destination)
        else:
            result = restore(args.backup_file, database)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
