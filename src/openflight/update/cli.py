"""``openflight-update`` command-line entry point.

openflight-update check     [--dry-run]                 # look for + prepare a newer release
openflight-update apply     [--tag TAG] [--dry-run]      # swap a prepared release in as active
openflight-update rollback                               # swap back to the previous release
openflight-update status                                 # active/pending/previous, last check/error
openflight-update bootstrap                              # migrate an existing git clone one time
"""

import argparse
from typing import List, Optional

from . import commands
from .client import GitHubReleaseClient
from .config import CONFIG_PATH, UpdateConfig, load_config


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        default=CONFIG_PATH,
        help=f"Path to update config/state (default: {CONFIG_PATH}).",
    )

    parser = argparse.ArgumentParser(
        prog="openflight-update",
        description="Auto-update OpenFlight from GitHub releases.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command")

    check_p = sub.add_parser(
        "check", parents=[common], help="Check for and prepare a newer release."
    )
    check_p.add_argument(
        "--dry-run", action="store_true", help="Report what would happen; change nothing."
    )

    apply_p = sub.add_parser(
        "apply", parents=[common], help="Swap a prepared release in as active."
    )
    apply_p.add_argument("--tag", default=None, help="Apply this tag instead of the pending one.")
    apply_p.add_argument(
        "--dry-run", action="store_true", help="Report what would happen; change nothing."
    )

    sub.add_parser("rollback", parents=[common], help="Swap back to the previous release.")
    sub.add_parser("status", parents=[common], help="Show current auto-update state.")
    bootstrap_p = sub.add_parser(
        "bootstrap", parents=[common], help="Migrate an existing git clone into the release layout."
    )
    bootstrap_p.add_argument(
        "--install-dir",
        default=None,
        help="Path of the existing git clone to migrate (default: config's install_dir).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    config_path = args.config
    config = load_config(config_path) or UpdateConfig()
    client = GitHubReleaseClient(config.repo)

    if args.command == "check":
        result = commands.cmd_check(config, config_path, client, dry_run=args.dry_run)
        return 1 if result.get("error") else 0

    if args.command == "apply":
        result = commands.cmd_apply(config, config_path, tag=args.tag, dry_run=args.dry_run)
        return 1 if result.get("error") else 0

    if args.command == "rollback":
        return 0 if commands.cmd_rollback(config, config_path) else 1

    if args.command == "status":
        commands.cmd_status(config)
        return 0

    if args.command == "bootstrap":
        if args.install_dir:
            config.install_dir = args.install_dir
        return 0 if commands.cmd_bootstrap(config, config_path) else 1

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
