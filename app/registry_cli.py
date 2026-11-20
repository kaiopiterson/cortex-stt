"""Local-only management of application credentials and limits (CortexLab STT v0.3.0).

Run inside the container, for example:
    docker compose exec stt python3 -m app.registry_cli list
"""

import argparse
import json
import sys

from app.core.config import get_settings
from app.core.registry import PRIORITY_CLASSES, AppRegistry

LIMIT_ARGUMENTS = (
    ("--rpm", "requests_per_minute"),
    ("--daily-seconds", "daily_audio_seconds"),
    ("--monthly-seconds", "monthly_audio_seconds"),
    ("--concurrency", "concurrency"),
)


def positive_or_default(value: str) -> int | None:
    """Parse a positive integer, or 'default' to clear the per-app override."""
    if value == "default":
        return None
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer or 'default'")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("register").add_argument("name")
    commands.add_parser("list")
    for action in ("rotate", "revoke", "revoke-tokens"):
        commands.add_parser(action).add_argument("app_id")
    limits = commands.add_parser("limits", help="set per-application limits")
    limits.add_argument("app_id")
    for flag, _ in LIMIT_ARGUMENTS:
        limits.add_argument(flag, type=positive_or_default, default=argparse.SUPPRESS)
    limits.add_argument("--priority", choices=PRIORITY_CLASSES)
    audit = commands.add_parser("audit", help="show recent administrative events")
    audit.add_argument("--limit", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = AppRegistry(get_settings().registry_path)
    registry.initialize()
    if args.command == "register":
        app_id, key = registry.register(args.name)
        registry.record_audit("register", app_id, "cli")
        print(json.dumps({"id": app_id, "api_key": key}))
    elif args.command == "rotate":
        key = registry.rotate(args.app_id)
        registry.record_audit("rotate", args.app_id, "cli")
        print(json.dumps({"id": args.app_id, "api_key": key}))
    elif args.command == "revoke":
        registry.revoke_app(args.app_id)
        registry.record_audit("revoke", args.app_id, "cli")
    elif args.command == "revoke-tokens":
        registry.revoke_tokens(args.app_id)
        registry.record_audit("revoke-tokens", args.app_id, "cli")
    elif args.command == "limits":
        changes: dict[str, int | str | None] = {}
        for flag, column in LIMIT_ARGUMENTS:
            attribute = flag.lstrip("-").replace("-", "_")
            if hasattr(args, attribute):
                changes[column] = getattr(args, attribute)
        if args.priority:
            changes["priority_class"] = args.priority
        if not changes:
            print("nothing to change", file=sys.stderr)
            return 2
        registry.set_limits(args.app_id, **changes)
        registry.record_audit("set-limits", args.app_id, "cli", json.dumps(changes))
        print(json.dumps({"id": args.app_id, "limits": changes}))
    elif args.command == "audit":
        print(json.dumps(registry.audit_events(args.limit), ensure_ascii=False))
    else:
        print(json.dumps(registry.list_apps(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
