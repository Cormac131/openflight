#!/usr/bin/env python3
"""Capture the two late looks on a connected IWR6843.

Start this at impact. It retunes to the long-range chirp, dumps the ring that
covers both looks, prints the measured slant ranges and descent angle, then
reloads the impact profile.
"""

from __future__ import annotations

import argparse
import json
import time

from openflight.iwr6843.calibration import DEFAULT_TEE_RANGE_M
from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.late_window import capture_late_window, plan_late_window


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=None)
    parser.add_argument("--ball-speed-mph", type=float, required=True)
    parser.add_argument("--launch-deg", type=float, required=True)
    parser.add_argument("--spin-rpm", type=float, required=True)
    parser.add_argument("--tee-m", type=float, default=DEFAULT_TEE_RANGE_M)
    parser.add_argument(
        "--restore-cfg",
        default="config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",
    )
    args = parser.parse_args()
    plan = plan_late_window(
        "outdoor",
        args.ball_speed_mph,
        args.launch_deg,
        args.spin_rpm,
        args.tee_m,
    )
    if not plan.enabled:
        raise SystemExit(f"no late window: {plan.reason}")
    radar = IWR6843Radar(args.port)
    try:
        measured = capture_late_window(
            radar,
            plan,
            impact_timestamp=time.time(),
            tee_range_m=args.tee_m,
            restore_cfg=args.restore_cfg,
            now=time.time,
            sleep=time.sleep,
        )
    finally:
        radar.close()
    if measured is None:
        raise SystemExit("first look had already passed")
    print(json.dumps(measured, indent=2))


if __name__ == "__main__":
    main()
