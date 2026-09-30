#!/usr/bin/env python3
"""Freeze the IWR6843 ring with ``l3dump`` and save it.

Stop the kiosk first. It owns this UART.

The command does not wait for the self-trigger. It snapshots whatever is in
the rolling ring when the dump starts, which is the pre-trigger window unless
a trigger has already frozen a longer movie. The adaptive profile keeps 24
pre-trigger frames (~72 ms); a timed cue is still useful when you want the
swing centred in that window.

    uv run python scripts/iwr6843/l3dump.py
    uv run python scripts/iwr6843/l3dump.py --out miss.l3dump
    uv run python scripts/iwr6843/l3dump.py --wait --count 5
    uv run python scripts/iwr6843/l3dump.py --loop --out dumps/
    uv run python scripts/iwr6843/l3dump.py --cue --count 5 --out dumps/
    uv run python scripts/iwr6843/l3dump.py --loop --cue --out dumps/
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.dump import HEADER, parse_header, payload_nbytes
from openflight.iwr6843.monitor import DEFAULT_IWR6843_CONFIG


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


def pre_trigger_window_s(config_path: str) -> float | None:
    """Configured pre-trigger duration from ``frameCfg`` and ``phaseCaptureCfg``, if present."""
    period_ms: float | None = None
    pre_frames: int | None = None
    with open(config_path, encoding="utf-8") as handle:
        for rawline in handle:
            line = rawline.strip()
            if not line or line.startswith("%"):
                continue
            parts = line.split()
            if parts[0] == "frameCfg" and len(parts) >= 6:
                period_ms = float(parts[5])
            elif parts[0] == "phaseCaptureCfg" and len(parts) >= 4:
                pre_frames = int(parts[3])
    if period_ms is None or pre_frames is None:
        return None
    return pre_frames * period_ms / 1000.0


def dump_label(sequence: int, count: int | None) -> str:
    """Human label for the Nth dump in a finite or open-ended run."""
    if count is None:
        return f"dump {sequence}"
    return f"dump {sequence}/{count}"


def cue_swing(
    sequence: int,
    count: int | None,
    countdown_s: float,
    swing_delay_s: float,
    *,
    pause=time.sleep,
    emit=print,
) -> None:
    """Countdown, call the swing, then wait ``swing_delay_s`` before the freeze."""
    label = dump_label(sequence, count)
    emit(f"{label}: get ready", flush=True)
    steps = max(1, int(round(countdown_s)))
    for remaining in range(steps, 0, -1):
        emit(f"{remaining}...", flush=True)
        pause(1.0)
    emit("SWING!", flush=True)
    if swing_delay_s > 0:
        pause(swing_delay_s)


def capture(
    radar: IWR6843Radar,
    count: int | None,
    settle_s: float,
    wait: bool,
    cue: bool,
    countdown_s: float,
    swing_delay_s: float,
    out: Path | None,
    *,
    clock=datetime.now,
    pause=time.sleep,
    prompt=input,
    emit=print,
) -> list[Path]:
    """Dump ``count`` times, or until Ctrl+C when ``count`` is None.

    ``--cue`` counts down and calls SWING before each freeze. ``--wait`` freezes
    on Enter. Otherwise each dump follows ``settle_s``.
    """
    written: list[Path] = []
    sequence = 0
    try:
        while count is None or sequence < count:
            sequence += 1
            if cue:
                cue_swing(
                    sequence,
                    count,
                    countdown_s,
                    swing_delay_s,
                    pause=pause,
                    emit=emit,
                )
            elif wait:
                prompt(f"{dump_label(sequence, count)}: press Enter to freeze the ring ")
            elif settle_s > 0:
                pause(settle_s)
            raw = radar.read_dump()
            path = output_path(out, clock(), sequence)
            metadata = save_dump(raw, path)
            emit(
                f"wrote {path} ({len(raw)} bytes, {metadata['n_frames']} frames)",
                flush=True,
            )
            written.append(path)
    except KeyboardInterrupt:
        emit(f"\nstopped after {len(written)} dumps", flush=True)
    return written


def effective_count(loop: bool, count: int | None) -> int | None:
    """Finite dump count, or None for an open-ended ``--loop`` run."""
    if count is not None:
        return count
    return None if loop else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", default=None, help="CLI port. Leave unset to probe; on Windows pass COMx."
    )
    parser.add_argument("--config", default=DEFAULT_IWR6843_CONFIG)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination .l3dump file, or a directory (default: the current directory)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="How many dumps to take (default: 1, or until Ctrl+C with --loop)",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Dump repeatedly until Ctrl+C (or until --count if set)",
    )
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
    parser.add_argument(
        "--cue",
        action="store_true",
        help="Countdown and call SWING before each dump, then freeze after --swing-delay-s",
    )
    parser.add_argument(
        "--countdown-s",
        type=float,
        default=3.0,
        help="Whole seconds counted down before SWING when --cue is set",
    )
    parser.add_argument(
        "--swing-delay-s",
        type=float,
        default=0.4,
        help="Seconds after SWING before freezing the ring",
    )
    args = parser.parse_args(argv)

    error = port_name_error(args.port, sys.platform)
    if error:
        raise SystemExit(error)
    if args.wait and args.cue:
        raise SystemExit("use --wait or --cue, not both")
    if args.count is not None and args.count < 1:
        raise SystemExit("--count must be at least 1")
    if args.settle_s < 0:
        raise SystemExit("--settle-s must be >= 0")
    if args.countdown_s <= 0:
        raise SystemExit("--countdown-s must be > 0")
    if args.swing_delay_s < 0:
        raise SystemExit("--swing-delay-s must be >= 0")
    count = effective_count(args.loop, args.count)
    if args.out is not None and args.out.suffix.lower() == ".l3dump" and count != 1:
        raise SystemExit("--out as a file needs a single dump; pass a directory for several")

    radar = IWR6843Radar(port=args.port)
    print(f"IWR6843 on {radar.port}", flush=True)
    if args.cue:
        window = pre_trigger_window_s(args.config)
        if window is not None:
            print(
                f"pre-trigger ring ~{window * 1000:.0f} ms; "
                f"swing on SWING, freeze {args.swing_delay_s:g}s later",
                flush=True,
            )
    try:
        radar.send_config(args.config)
        capture(
            radar,
            count,
            args.settle_s,
            args.wait,
            args.cue,
            args.countdown_s,
            args.swing_delay_s,
            args.out,
        )
    finally:
        try:
            radar.stop_sensor()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        radar.close()


if __name__ == "__main__":
    main()
