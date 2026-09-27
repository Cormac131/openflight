#!/usr/bin/env python3
"""Settle how the IWR6843 HWA rounds its IQ8 output shift, on a board.

``l3_iq8.c`` models the HWA's ``dstScale`` right shift either as a
truncation (floor) or as round-half-up; the source cannot tell which the
silicon does, and the difference is a half-step bias on every stored IQ8
component. This probe captures a static scene with an IQ8 profile and reads
the int8 components' mean: a range-FFT component of a static scene averages
to zero over enough chirps, so a mean near -0.5 says the shift truncates and
a mean near 0 says it rounds. Pass the verdict to
``scripts/analysis/ab_iq16_iq8.py --hwa-rounding`` (or leave it off) so the
offline IQ8 is the board's.

Usage::

    uv run python scripts/hardware-test/iwr6843_iq8_hwa_probe.py \\
        --config config/iwr6843_l3dump_dense_45f2ms_53bin_iq8.cfg

Keep the lane empty and still while it runs: a mover's residual has a
nonzero mean over a frame.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

sys.path.insert(0, "src")

from openflight.iwr6843.driver import IWR6843Radar  # noqa: E402
from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED, parse_dump  # noqa: E402
from openflight.iwr6843.iq8_emulation import hwa_rounding_from_bias  # noqa: E402


def int8_components(raw: bytes) -> np.ndarray:
    """The dump's int8 payload, exactly as stored (no scale applied)."""
    meta, _cube = parse_dump(raw)
    if meta["sample_fmt"] != SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED:
        raise ValueError("the probe needs an IQ8 capture profile (captureFormat iq8)")
    offset = meta["header_nbytes"] + meta["frame_metadata_nbytes"]
    total = sum(meta["range_bin_counts"]) * meta["chirps_per_frame"] * meta["n_rx"] * 2
    return np.frombuffer(raw, dtype=np.int8, offset=offset, count=total)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--config", required=True, help="an IQ8 .cfg profile")
    parser.add_argument("--port", default=None, help="serial port (default: auto-detect)")
    parser.add_argument("--captures", type=int, default=3, help="dumps to average over")
    parser.add_argument("--settle-s", type=float, default=1.0, help="seconds before each dump")
    args = parser.parse_args()

    components = []
    with IWR6843Radar(port=args.port) as radar:
        print(f"IWR6843 on {radar.port}")
        radar.send_config(args.config)
        for index in range(args.captures):
            time.sleep(args.settle_s)
            raw = radar.read_dump()
            values = int8_components(raw)
            components.append(values)
            print(f"capture {index + 1}: {values.size} components, mean {values.mean():+.3f}")
            if index + 1 < args.captures:
                radar.send_config(args.config)
        radar.stop_sensor()
    verdict = hwa_rounding_from_bias(np.concatenate(components))
    print(f"int8 mean {verdict.bias_lsb:+.3f} over {verdict.components} components")
    if verdict.rounding == "unclear":
        print("UNCLEAR: bias between the two models; try more captures or a stiller scene")
        return 1
    flag = (
        "--hwa-rounding"
        if verdict.rounding == "round"
        else "(no --hwa-rounding: the shift truncates)"
    )
    print(f"HWA shift {verdict.rounding}s -> replay IQ8 with {flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
