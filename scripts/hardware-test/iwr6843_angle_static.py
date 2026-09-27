#!/usr/bin/env python3
"""Static angular validation of the IWR6843 against a corner reflector (phase 14).

Place a corner reflector at a known azimuth and elevation from the radar's
boresight (a tape measure and the radar height give the angles), start the
firmware's ball detector so it locks on the stationary return, and read its
measured direction (``ball status`` -> ``ballangle``) many times. Repeat for
every position the protocol names (azimuth -20..+20 in 5 degree steps at 0
elevation, then elevation -15..+15 in 5 degree steps at 0 azimuth), and
again after re-placing the reflector for repeatability. The samples go to a
JSON file that ``openflight.iwr6843.angle_validation`` summarises: per
position bias, standard deviation, P95, quality numbers and the
repeatability across placements.

Usage::

    uv run python scripts/hardware-test/iwr6843_angle_static.py \\
        --config config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg \\
        --out angle_static_iq16.json --samples 30

Then compare two captures (IQ16 and IQ8 profiles) with::

    uv run python scripts/analysis/iwr6843_angle_moving.py --static \\
        angle_static_iq16.json angle_static_iq8.json

Load the calibration (``trackCfg cal`` / ``trackCfg elem``) before this runs
if the numbers are to mean anything beyond a repeatability check: without
it, systematic channel error is measured, not the estimator.
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "src")

from openflight.iwr6843.angle_validation import (  # noqa: E402
    STATIC_AZIMUTHS_DEG,
    STATIC_ELEVATIONS_DEG,
    AngleSample,
    ValidationSet,
    format_positions,
    summarize_positions,
)
from openflight.iwr6843.driver import IWR6843Radar  # noqa: E402
from openflight.iwr6843.tee_scan import parse_ball_status  # noqa: E402


def positions(args) -> list[tuple[float, float]]:
    if args.positions:
        out = []
        for text in args.positions:
            az, el = (float(v) for v in text.split(","))
            out.append((az, el))
        return out
    return [(az, 0.0) for az in STATIC_AZIMUTHS_DEG] + [
        (0.0, el) for el in STATIC_ELEVATIONS_DEG if el != 0.0
    ]


def collect(
    radar: IWR6843Radar, truth_az: float, truth_el: float, count: int, label: str, pause_s: float
) -> list[AngleSample]:
    samples: list[AngleSample] = []
    misses = 0
    while len(samples) < count and misses < 3 * count:
        time.sleep(pause_s)
        status = parse_ball_status(radar.ball_status())
        if not status.locked or status.angle is None:
            misses += 1
            continue
        angle = status.angle
        samples.append(
            AngleSample(
                truth_azimuth_deg=truth_az,
                truth_elevation_deg=truth_el,
                azimuth_deg=angle.azimuth_deg,
                elevation_deg=angle.elevation_deg if angle.trusted else None,
                coherence=angle.azimuth_coherence,
                peak_ratio=angle.elevation_peak_ratio,
                confidence=angle.confidence,
                speed_mps=0.0,
                label=label,
            )
        )
    if misses:
        print(f"  {misses} reads without a trusted lock")
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--config", required=True, help="capture profile to run")
    parser.add_argument("--port", default=None)
    parser.add_argument("--out", required=True, help="JSON file for the samples")
    parser.add_argument("--samples", type=int, default=30, help="reads per position")
    parser.add_argument("--pause-s", type=float, default=0.2, help="seconds between reads")
    parser.add_argument(
        "--label", default=None, help="sample label (default: the profile's format)"
    )
    parser.add_argument("--firmware-sha", default=None)
    parser.add_argument(
        "--positions",
        nargs="*",
        default=None,
        metavar="AZ,EL",
        help="truth positions in degrees instead of the protocol's list",
    )
    parser.add_argument("--repeats", type=int, default=1, help="placements per position")
    args = parser.parse_args()

    label = args.label or ("iq8" if "iq8" in args.config else "iq16")
    result = ValidationSet("static", firmware_sha=args.firmware_sha, notes=args.config)
    with IWR6843Radar(port=args.port) as radar:
        print(f"IWR6843 on {radar.port}")
        radar.send_config(args.config)
        radar.configure_ball(True, False)
        for truth_az, truth_el in positions(args):
            for repeat in range(args.repeats):
                input(
                    f"\nPlace the reflector at azimuth {truth_az:+.1f}, elevation {truth_el:+.1f} "
                    f"(placement {repeat + 1}/{args.repeats}), leave the lane, press Enter"
                )
                time.sleep(2.0)  # let the detector settle and lock
                samples = collect(radar, truth_az, truth_el, args.samples, label, args.pause_s)
                result.samples.extend(samples)
                print(f"  {len(samples)} samples")
                result.save(args.out)
        radar.stop_sensor()
    print()
    print(format_positions(summarize_positions(result.samples)))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
