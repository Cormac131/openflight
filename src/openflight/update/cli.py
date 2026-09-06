"""``openflight-update`` command-line entry point.

openflight-update status [--json]              # what is installed, staged, available
openflight-update check                        # look for a newer release on the channel
openflight-update set-channel stable|experimental|off
"""

import argparse
import json
import logging
import sys
from typing import List, Optional

from ..release import get_release_info
from .config import UpdateConfig, load_update_config, save_update_config, validate_channel
from .paths import resolve_update_paths
from .status import compose_update_status

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOTHING_TO_DO = 2


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--install-link", default=None, help="Install symlink (default: ~/openflight)"
    )
    common.add_argument(
        "--releases-root", default=None, help="Releases directory (default: <link>-releases)"
    )
    common.add_argument("--verbose", "-v", action="store_true", help="Log what the command does")

    parser = argparse.ArgumentParser(
        prog="openflight-update",
        description="Follow a release channel and stage new OpenFlight releases.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status", parents=[common], help="Show update state.")
    status.add_argument("--json", action="store_true", help="Print the status as JSON.")

    sub.add_parser("check", parents=[common], help="Look for a newer release on the channel.")

    set_channel = sub.add_parser(
        "set-channel", parents=[common], help="Choose the channel to follow."
    )
    set_channel.add_argument("channel", choices=["stable", "experimental", "off"])
    set_channel.add_argument("--repository", default=None, help="GitHub owner/name to follow.")
    return parser


def _print_status(status: dict) -> None:
    current = status["current"]
    print(f"State:      {status['state']}")
    print(f"Channel:    {status['channel'] or 'off'} ({status['repository']})")
    print(f"Current:    {current['version']} ({current['channel']})")
    for key in ("available", "staged", "previous"):
        entry = status.get(key)
        label = f"{key.capitalize()}:".ljust(12)
        print(f"{label}{entry.get('tag') or entry.get('name') if entry else 'none'}")
    print(f"Last check: {status['last_check_at'] or 'never'}")
    if status["error"]:
        print(f"Error:      {status['error']}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_ERROR
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    paths = resolve_update_paths(install_link=args.install_link, releases_root=args.releases_root)
    installed = get_release_info()

    if args.command == "status":
        status = compose_update_status(paths, installed)
        if args.json:
            json.dump(status, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            _print_status(status)
        return EXIT_OK

    if args.command == "check":
        from .check import run_check  # pylint: disable=import-outside-toplevel

        return run_check(paths, installed=installed)

    if args.command == "set-channel":
        channel = validate_channel(None if args.channel == "off" else args.channel)
        existing = load_update_config(paths.config, installed)
        repository = (args.repository or existing.repository).strip()
        save_update_config(UpdateConfig(channel=channel, repository=repository), paths.config)
        print(f"Channel: {channel or 'off'} ({repository})")
        return EXIT_OK

    parser.print_help()
    return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
