#!/usr/bin/env python3
"""Angular validation from recordings of a moving reflector, and static A/B (phases 14-15).

Moving: a reflector swings through the lane (a pendulum, or a ball on a
string) while captures are recorded. Each ``.l3dump`` is replayed through
the firmware club track and angle estimator and every tracked point's
azimuth and elevation are compared with the reflector's known direction,
given per file in the manifest (``truth_azimuth_deg``, ``truth_elevation_deg``)
or on the command line. The error is then binned by radial speed, which is
where the TDM phase correction and the Doppler alias resolution are
exercised, and the whole thing is repeated on the firmware-exact IQ8 of the
same captures (``--iq8``) so IQ16 and IQ8 sit side by side.

Static: two or more JSON files from ``iwr6843_angle_static.py`` (one per
profile) are compared position by position.

Usage::

    uv run python scripts/analysis/iwr6843_angle_moving.py pendulum/ --truth 0,0 --tee-bin 34 --iq8
    uv run python scripts/analysis/iwr6843_angle_moving.py --static static_iq16.json static_iq8.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from openflight.iwr6843 import firmware_host as fw
from openflight.iwr6843.angle_validation import (
    AngleSample,
    ValidationSet,
    compare_paths,
    error_by_speed,
    format_comparison,
    format_positions,
    format_speed_bins,
    summarize_positions,
)
from openflight.iwr6843.firmware_replay import ReplayConfig, ReplayResult, replay_dump
from openflight.iwr6843.iq8_emulation import Iq8Mode, emulate_dump

SPEED_EDGES_MPS = (0.0, 2.0, 5.0, 10.0, 20.0, 40.0)


def samples_from_replay(
    result: ReplayResult, truth_az: float, truth_el: float, label: str
) -> list[AngleSample]:
    """One sample per tracked point that carried an angle estimate."""
    out: list[AngleSample] = []
    by_frame = {frame.frame: frame for frame in result.frames}
    for point in list(result.points) + list(result.ball_points):
        frame = by_frame.get(point.frame)
        if frame is None or frame.angle is None:
            continue
        out.append(
            AngleSample(
                truth_azimuth_deg=truth_az,
                truth_elevation_deg=truth_el,
                azimuth_deg=frame.angle.azimuth_deg,
                elevation_deg=frame.angle.elevation_deg,
                coherence=frame.angle.azimuth_coherence,
                peak_ratio=frame.angle.elevation_peak_ratio,
                confidence=frame.angle.confidence,
                speed_mps=point.doppler_mps,
                label=label,
            )
        )
    return out


def _truths(directory: Path, default: tuple[float, float] | None) -> dict[str, tuple[float, float]]:
    manifest = directory / "manifest.json"
    truths: dict[str, tuple[float, float]] = {}
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for name, entry in data.items():
            if isinstance(entry, dict) and "truth_azimuth_deg" in entry:
                truths[name] = (
                    float(entry["truth_azimuth_deg"]),
                    float(entry.get("truth_elevation_deg", 0.0)),
                )
    if default is not None:
        for path in directory.glob("*.l3dump"):
            truths.setdefault(path.name, default)
    return truths


def run_moving(args) -> int:
    default = None
    if args.truth:
        az, el = (float(v) for v in args.truth.split(","))
        default = (az, el)
    lib = fw.build_firmware_library()
    iq16: list[AngleSample] = []
    iq8: list[AngleSample] = []
    for text in args.paths:
        path = Path(text)
        files = sorted(path.glob("*.l3dump")) if path.is_dir() else [path]
        truths = _truths(path if path.is_dir() else path.parent, default)
        for file in files:
            truth = truths.get(file.name)
            if truth is None:
                print(
                    f"{file.name}: no truth direction (manifest truth_azimuth_deg or --truth); skipped"
                )
                continue
            config = ReplayConfig(tee_bin=args.tee_bin, dest_bin=args.dest_bin, post_impact=False)
            raw = file.read_bytes()
            iq16.extend(samples_from_replay(replay_dump(raw, config, lib=lib), *truth, "iq16"))
            if args.iq8:
                mode = Iq8Mode(args.iq8_path)
                emulated = emulate_dump(raw, mode, lib=lib)
                iq8.extend(
                    samples_from_replay(replay_dump(emulated, config, lib=lib), *truth, mode.label)
                )
    if not iq16:
        print("no angle samples", file=sys.stderr)
        return 1
    print(f"== IQ16: {len(iq16)} angle samples")
    print(format_speed_bins(error_by_speed(iq16, SPEED_EDGES_MPS)))
    if iq8:
        print(f"\n== {iq8[0].label}: {len(iq8)} angle samples")
        print(format_speed_bins(error_by_speed(iq8, SPEED_EDGES_MPS)))
        print("\n== IQ16 vs IQ8 over every sample")
        print(format_comparison(compare_paths(iq16, iq8)))
    if args.out:
        result = ValidationSet("moving", notes=" ".join(args.paths), samples=iq16 + iq8)
        result.save(args.out)
        print(f"\nwrote {args.out}")
    return 0


def run_static(args) -> int:
    sets = [ValidationSet.load(path) for path in args.paths]
    for vs, path in zip(sets, args.paths, strict=True):
        print(f"== {path} ({vs.protocol}, firmware {vs.firmware_sha or '?'})")
        print(format_positions(summarize_positions(vs.samples)))
        print()
    if len(sets) >= 2:
        print("== first vs second")
        print(format_comparison(compare_paths(sets[0].samples, sets[1].samples)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument(
        "paths", nargs="+", help=".l3dump files/directories, or JSON sets with --static"
    )
    parser.add_argument(
        "--static", action="store_true", help="compare static validation JSON files"
    )
    parser.add_argument(
        "--truth", default=None, metavar="AZ,EL", help="truth direction for every file"
    )
    parser.add_argument(
        "--tee-bin", type=int, default=34, help="global bin the reflector swings around"
    )
    parser.add_argument("--dest-bin", type=int, default=None)
    parser.add_argument("--iq8", action="store_true", help="also replay the firmware-exact IQ8")
    parser.add_argument("--iq8-path", choices=("cpu", "edma", "dump"), default="edma")
    parser.add_argument("--out", type=Path, default=None, help="save the samples as JSON")
    args = parser.parse_args(argv)
    return run_static(args) if args.static else run_moving(args)


if __name__ == "__main__":
    sys.exit(main())
