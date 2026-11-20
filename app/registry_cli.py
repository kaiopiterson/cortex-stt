"""Local-only management of application credentials."""

import argparse
import json

from app.core.config import get_settings
from app.core.registry import AppRegistry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("register")
    create.add_argument("name")
    commands.add_parser("list")
    for action in ("rotate", "revoke", "revoke-tokens"):
        commands.add_parser(action).add_argument("app_id")
    args = parser.parse_args()
    registry = AppRegistry(get_settings().registry_path)
    registry.initialize()
    if args.command == "register":
        app_id, key = registry.register(args.name)
        print(json.dumps({"id": app_id, "api_key": key}))
    elif args.command == "rotate":
        print(json.dumps({"id": args.app_id, "api_key": registry.rotate(args.app_id)}))
    elif args.command == "revoke":
        registry.revoke_app(args.app_id)
    elif args.command == "revoke-tokens":
        registry.revoke_tokens(args.app_id)
    else:
        print(json.dumps(registry.list_apps(), ensure_ascii=False))


if __name__ == "__main__":
    main()
