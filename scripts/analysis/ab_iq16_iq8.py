#!/usr/bin/env python3
"""IQ16 vs firmware-exact IQ8: replay each recording both ways and compare.

For every IQ16 ``.l3dump`` the capture is quantised the way the board's IQ8
path stores it (``openflight.iwr6843.iq8_emulation``: the same C as the
firmware, so the emulated IQ8 is the board's IQ8, scale, rounding, clip and
wrap included), both are replayed through the firmware trigger, club track,
impact detector, ball track and launch fit, and the measurements are put
side by side. A summary over the corpus follows: how often IQ8 changed a
decision and by how much it moved each measurement.

Usage::

    uv run python scripts/analysis/ab_iq16_iq8.py tests/radar/recordings
    uv run python scripts/analysis/ab_iq16_iq8.py capture.l3dump --tee-bin 34 --path edma --hwa-shift 7
    uv run python scripts/analysis/ab_iq16_iq8.py tests/radar/recordings --json ab.json

``--path`` selects the firmware IQ8 path to emulate: ``cpu`` (per-frame
shift), ``edma`` (fixed iq8Scale shift, low-byte copy) or ``dump`` (dump-time
divide). ``--hwa-rounding`` models an HWA that rounds before shifting; see
scripts/hardware-test/iwr6843_iq8_hwa_probe.py to settle that on a board.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.ab_compare import (
    aggregate,
    compare_replays,
    format_aggregate,
    format_table,
    rows_to_dict,
)
from openflight.iwr6843.firmware_replay import (
    DEFAULT_FFT_SIZE,
    ReplayConfig,
    recording_configs,
    replay_dump,
)
from openflight.iwr6843.iq8_emulation import PATHS, Iq8Mode, emulate_dump, quantisation_report
from openflight.iwr6843.tracking import RANGE_SPAN_M


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("paths", nargs="+", help=".l3dump files or directories of them")
    tee = parser.add_mutually_exclusive_group()
    tee.add_argument("--tee-bin", type=int, help="global range bin of the tee")
    tee.add_argument("--tee-range-m", type=float, help="tee range in metres (converted to a bin)")
    parser.add_argument("--fft-size", type=int, default=DEFAULT_FFT_SIZE)
    parser.add_argument(
        "--path",
        choices=PATHS,
        default="edma",
        help="firmware IQ8 path to emulate (edma is the shipped build: L3_IQ8_EDMA_PACK)",
    )
    parser.add_argument(
        "--hwa-shift", type=int, default=None, help="HWA output shift (path default)"
    )
    parser.add_argument("--hwa-rounding", action="store_true", help="model a rounding HWA shift")
    parser.add_argument("--stride", type=int, default=None, help="cpu path: preview stride")
    parser.add_argument("--json", type=Path, default=None, help="write every table as JSON")
    parser.add_argument("--quiet", action="store_true", help="only the summary")
    return parser


def _files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for text in paths:
        path = Path(text)
        files.extend(sorted(path.glob("*.l3dump")) if path.is_dir() else [path])
    return files


def _configs(files: list[Path], args) -> dict[Path, ReplayConfig]:
    default_tee = args.tee_bin
    if default_tee is None and args.tee_range_m is not None:
        default_tee = round(args.tee_range_m / (RANGE_SPAN_M / args.fft_size))
    configs: dict[Path, ReplayConfig] = {}
    by_dir: dict[Path, dict[str, ReplayConfig]] = {}
    for file in files:
        directory = file.parent
        if directory not in by_dir:
            try:
                by_dir[directory] = {
                    path.name: config
                    for path, config in recording_configs(directory, default_tee_bin=default_tee)
                }
            except (FileNotFoundError, ValueError):
                by_dir[directory] = {}
        config = by_dir[directory].get(file.name)
        if config is None:
            if default_tee is None:
                raise SystemExit(f"{file.name}: no manifest entry; give --tee-bin or --tee-range-m")
            config = ReplayConfig(tee_bin=default_tee, fft_size=args.fft_size)
        configs[file] = config
    return configs


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    files = _files(args.paths)
    if not files:
        print("no .l3dump files", file=sys.stderr)
        return 2
    configs = _configs(files, args)
    mode = Iq8Mode(
        args.path,
        hwa_shift=args.hwa_shift,
        hwa_rounding=args.hwa_rounding,
        sparse_stride=args.stride,
    )
    lib = fw.build_firmware_library()
    tables = []
    report_json = []
    for file in files:
        raw = file.read_bytes()
        try:
            emulated = emulate_dump(raw, mode, lib=lib)
        except ValueError as exc:
            print(f"{file.name}: skipped ({exc})")
            continue
        config = configs[file]
        result16 = replay_dump(raw, config, lib=lib)
        result8 = replay_dump(emulated, config, lib=lib)
        rows = compare_replays(result16, result8)
        tables.append(rows)
        quant = quantisation_report(raw, mode, lib=lib)
        scales = sorted({q.scale for q in quant})
        clipped = sum(q.clipped for q in quant)
        rms = max(q.rms_error_lsb for q in quant) if quant else 0.0
        if not args.quiet:
            print(
                f"== {file.name}  ({mode.label}, scales {scales}, clipped {clipped}, worst rms {rms:.1f} LSB)"
            )
            print(format_table(rows, b_label=mode.label))
            print()
        report_json.append(
            {
                "file": file.name,
                "mode": mode.label,
                "scales": scales,
                "clipped_components": clipped,
                "rows": rows_to_dict(rows),
            }
        )
    if not tables:
        return 1
    print(f"== summary over {len(tables)} captures ({mode.label})")
    print(format_aggregate(aggregate(tables), b_label=mode.label))
    if args.json is not None:
        args.json.write_text(json.dumps(report_json, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
