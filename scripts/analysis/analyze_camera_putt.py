#!/usr/bin/env python3
"""Replay saved rear-camera captures through the camera-only putt estimator.

Each capture directory (or ``frames.npz``) is searched with the putting profile:
no OPS243 speed gate, a flat path search, and depth from the apparent ball
size. Geometry comes from a tape measure rather than the IWR6843 tee
calibration, so pass the camera height and the horizontal camera-to-ball
distance measured at address.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from openflight.camera.ball_flight import CameraBallGeometry, estimate_camera_putt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "captures",
        nargs="+",
        type=Path,
        help="Camera capture directories or frames.npz files",
    )
    parser.add_argument(
        "--ball-distance-m",
        required=True,
        type=float,
        help="Horizontal distance from the camera lens to the ball at address",
    )
    parser.add_argument(
        "--camera-height-m",
        type=float,
        default=0.20955,
        help="Camera optical-center height above the putting surface (default: 8.25 in)",
    )
    parser.add_argument(
        "--ball-height-m",
        type=float,
        default=None,
        help="Ball center height above the surface (default: a ball resting on the ground)",
    )
    parser.add_argument("--lateral-offset-m", type=float, default=0.0)
    parser.add_argument("--horizontal-offset-deg", type=float, default=0.0)
    parser.add_argument("--roll-deg", type=float, default=0.0)
    parser.add_argument("--mirror-horizontal", action="store_true")
    parser.add_argument("--json", action="store_true", help="Print one JSON object per capture")
    return parser


def _frames_path(capture: Path) -> Path:
    return capture if capture.is_file() else capture / "frames.npz"


def analyze_capture(path: Path, args: argparse.Namespace) -> dict:
    """Return the putt estimate for one saved capture as a plain dict."""
    with np.load(_frames_path(path)) as archive:
        frames = archive["frames"]
        timestamps_ns = archive["host_timestamp_ns"]
        trigger_ns = int(archive["trigger_host_timestamp_ns"])
    overrides = {
        "camera_lateral_offset_m": args.lateral_offset_m,
        "horizontal_offset_deg": args.horizontal_offset_deg,
        "roll_correction_deg": args.roll_deg,
        "horizontal_pixel_sign": -1.0 if args.mirror_horizontal else 1.0,
        "image_width_px": int(frames.shape[2]),
        "image_height_px": int(frames.shape[1]),
    }
    if args.ball_height_m is not None:
        overrides["ball_height_m"] = args.ball_height_m
    geometry = CameraBallGeometry.from_camera_measurements(
        camera_height_m=args.camera_height_m,
        ball_forward_m=args.ball_distance_m,
        **overrides,
    )
    estimate = estimate_camera_putt(
        frames,
        timestamps_ns,
        trigger_ns=trigger_ns,
        geometry=geometry,
    )
    return {
        "capture": str(path),
        "status": estimate.status,
        "confidence_tier": estimate.confidence_tier,
        "ball_speed_mph": estimate.speed_mph,
        "start_line_deg": estimate.horizontal_deg,
        "vertical_deg": estimate.vertical_deg,
        "support": estimate.support,
        "parameter_mad_deg": estimate.parameter_mad_deg,
        "window_mad_deg": estimate.window_mad_deg,
        "n_points": estimate.n_points,
        "first_frame": estimate.first_frame,
        "last_frame": estimate.last_frame,
        "frames_in_capture": int(frames.shape[0]),
    }


def _format(result: dict) -> str:
    speed = result["ball_speed_mph"]
    line = result["start_line_deg"]
    return (
        f"{result['capture']}: {result['status']} ({result['confidence_tier']}) "
        f"speed={'n/a' if speed is None else f'{speed:.2f} mph'} "
        f"start_line={'n/a' if line is None else f'{line:+.2f} deg'} "
        f"support={result['support']}/27 points={result['n_points']} "
        f"frames={result['first_frame']}..{result['last_frame']}"
    )


def main() -> None:
    """Run the putt estimator over every capture on the command line."""
    args = _parser().parse_args()
    for capture in args.captures:
        result = analyze_capture(capture, args)
        print(json.dumps(result) if args.json else _format(result))


if __name__ == "__main__":
    main()
