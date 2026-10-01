"""``openflight-firmware`` command-line entry point.

openflight-firmware show                  # release version in firmware/VERSION
openflight-firmware bump patch            # bump firmware/VERSION (patch/minor/major)
openflight-firmware query [--port PORT]   # version on the flashed board
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from openflight.iwr6843 import driver
from openflight.iwr6843.firmware_version import (
    BUMP_PARTS,
    VERSION_FILE,
    bump_release_version,
    read_release_version,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openflight-firmware",
        description="IWR6843 firmware release version and flashed-image query.",
    )
    parser.add_argument(
        "--version-file",
        type=Path,
        default=VERSION_FILE,
        help=f"Release version file (default: {VERSION_FILE}).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="Print the release version in firmware/VERSION.")
    bump = sub.add_parser("bump", help="Bump the release version in firmware/VERSION.")
    bump.add_argument("part", choices=BUMP_PARTS)
    query = sub.add_parser("query", help="Ask the flashed board for its firmware version.")
    query.add_argument("--port", help="IWR6843 CLI port (default: auto-detect).")
    return parser


def _query(port: Optional[str]) -> int:
    try:
        with driver.IWR6843Radar(port=port) as radar:
            flashed = radar.firmware_version()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Could not read the firmware version: {exc}", file=sys.stderr)
        return 1
    if flashed is None:
        print("Flashed firmware predates stats version (unversioned image).")
        return 0
    print(f"Flashed firmware: {flashed}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point for ``openflight-firmware``."""
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "show":
            print(read_release_version(args.version_file))
            return 0
        if args.command == "bump":
            old, new = bump_release_version(args.part, args.version_file)
            print(f"{old} -> {new}")
            return 0
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return _query(args.port)


if __name__ == "__main__":
    sys.exit(main())
