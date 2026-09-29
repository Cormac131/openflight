#!/usr/bin/env python3
"""Freeze the IWR6843 ring with ``l3dump`` and save it.

Stop the kiosk first. It owns this UART.

The command does not wait for the self-trigger. It snapshots whatever is in
the rolling ring when the dump starts, which is the pre-trigger window unless
a trigger has already frozen a longer movie. Swing, then run it immediately:
the wide profile's pre-trigger ring is only a few frames long.

    uv run python scripts/iwr6843/l3dump.py
    uv run python scripts/iwr6843/l3dump.py --out miss.l3dump
    uv run python scripts/iwr6843/l3dump.py --wait --count 5
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.dump import HEADER, parse_header, payload_nbytes

_DEFAULT_CFG = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"


def port_name_error(port: str | None, platform: str) -> str | None:
    """Windows ``COMn`` names are not device paths on the Pi."""
    if not port or platform == "win32":
        return None
    suffix = port[3:]
    if port.upper().startswith("COM") and suffix.isdigit():
        return (
            f"{port} is a Windows port name. On this machine leave --port off, "
            "or pass a device path such as /dev/ttyUSB0."
        )
    return None


def output_path(out: Path | None, when: datetime, sequence: int) -> Path:
    """One ``.l3dump`` path. A file target is used as given; anything else is a directory."""
    if out is not None and out.suffix.lower() == ".l3dump":
        return out
    directory = Path.cwd() if out is None else out
    timestamp = when.strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return directory / f"iwr6843_{timestamp}_{sequence:03d}.l3dump"


def validated_dump(raw: bytes) -> dict:
    """Header metadata for one complete dump, or ``ValueError`` when the transfer is short."""
    if len(raw) < HEADER.size:
        raise ValueError(f"short IWR6843 dump: {len(raw)} bytes")
    metadata = parse_header(raw)
    expected = metadata["header_nbytes"] + payload_nbytes(metadata, raw)
    if len(raw) != expected:
        raise ValueError(f"short IWR6843 dump: {len(raw)} bytes, expected {expected}")
    return metadata


def save_dump(raw: bytes, path: Path) -> dict:
    """Check ``raw`` and write it to ``path``. Returns the parsed header."""
    metadata = validated_dump(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return metadata


def capture(
    radar: IWR6843Radar,
    count: int,
    settle_s: float,
    wait: bool,
    out: Path | None,
    *,
    clock=datetime.now,
    pause=time.sleep,
    prompt=input,
) -> list[Path]:
    """Dump ``count`` times. ``--wait`` freezes on Enter; otherwise each dump follows ``settle_s``."""
    written: list[Path] = []
    for sequence in range(1, count + 1):
        if wait:
            prompt(f"dump {sequence}/{count}: press Enter to freeze the ring ")
        elif settle_s > 0:
            pause(settle_s)
        raw = radar.read_dump()
        path = output_path(out, clock(), sequence)
        metadata = save_dump(raw, path)
        print(
            f"wrote {path} ({len(raw)} bytes, {metadata['n_frames']} frames)",
            flush=True,
        )
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", default=None, help="CLI port. Leave unset to probe; on Windows pass COMx."
    )
    parser.add_argument("--config", default=_DEFAULT_CFG)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination .l3dump file, or a directory (default: the current directory)",
    )
    parser.add_argument("--count", type=int, default=1, help="How many dumps to take")
    parser.add_argument(
        "--settle-s",
        type=float,
        default=0.5,
        help="Seconds to let the ring fill before each automatic dump",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Wait for Enter before each dump instead of dumping on a timer",
    )
    args = parser.parse_args(argv)

    error = port_name_error(args.port, sys.platform)
    if error:
        raise SystemExit(error)
    if args.count < 1:
        raise SystemExit("--count must be at least 1")
    if args.settle_s < 0:
        raise SystemExit("--settle-s must be >= 0")
    if args.out is not None and args.out.suffix.lower() == ".l3dump" and args.count != 1:
        raise SystemExit("--out as a file needs --count 1; pass a directory for several dumps")

    radar = IWR6843Radar(port=args.port)
    print(f"IWR6843 on {radar.port}", flush=True)
    try:
        radar.send_config(args.config)
        capture(radar, args.count, args.settle_s, args.wait, args.out)
    finally:
        try:
            radar.stop_sensor()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        radar.close()


if __name__ == "__main__":
    main()
