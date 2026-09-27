"""Stationary-ball detection from the firmware's ``teeScan`` static power.

The self-trigger works from the MTI residual, which removes a ball sitting
on the tee entirely, so it can never say whether the radar sees the ball at
all. ``teeScan`` reports the raw static power per range bin instead; this
module parses it, averages repeated scans, and compares an empty tee against
an occupied one to find where the ball actually is relative to where the
configured tee range says it should be.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from openflight.iwr6843.tracking import RANGE_SPAN_M

_HEADER = re.compile(r"teescan frames=(\d+) loops=(\d+) first=(\d+) count=(\d+) start=(\d+)")
_BIN = re.compile(r"^bin=(\d+) power=(\d+)$")

# Scans averaged per condition: 6 x 9 pre frames on the wide profile.
DEFAULT_SCANS = 6
# Bins searched either side of the expected tee bin: about 28 cm at 4.7 cm bins.
DEFAULT_SEARCH_HALF_WIDTH = 6
# Occupied/baseline power at the detected bin that counts as "a ball is there".
# Deliberately loose until real tees have been measured; the report carries
# the ratio either way.
DEFAULT_MIN_RATIO = 1.5


@dataclass(frozen=True)
class TeeScan:
    """One ``teeScan`` reply: mean static power per local bin over ``frames``."""

    frames: int
    loops: int
    first: int
    count: int
    window_start: int  # absolute FFT bin of local bin 0
    power: dict[int, float]


def parse_tee_scan(text: str) -> TeeScan:
    """Parse a ``teeScan`` reply; raises ValueError when the header is missing."""
    header = _HEADER.search(text)
    if header is None:
        raise ValueError(f"no teescan header in {text.strip()[:80]!r}")
    frames, loops, first, count, start = (int(group) for group in header.groups())
    power: dict[int, float] = {}
    for line in text.splitlines():
        match = _BIN.match(line.strip())
        if match:
            power[int(match.group(1))] = float(match.group(2))
    if len(power) != count:
        raise ValueError(f"teeScan promised {count} bins, parsed {len(power)}")
    return TeeScan(frames, loops, first, count, start, power)


def average_scans(scans: list[TeeScan]) -> dict[int, float]:
    """Frame-weighted mean static power per bin across scans of the same run."""
    if not scans:
        raise ValueError("no scans to average")
    total_frames = sum(scan.frames for scan in scans)
    if total_frames == 0:
        raise ValueError("teeScan saw no pre-trigger frames; is the sensor running?")
    bins = set(scans[0].power)
    for scan in scans[1:]:
        if set(scan.power) != bins:
            raise ValueError("teeScan replies cover different bins")
    return {
        local_bin: sum(scan.power[local_bin] * scan.frames for scan in scans) / total_frames
        for local_bin in sorted(bins)
    }


@dataclass(frozen=True)
class BallDetection:
    """Where the occupied tee differs most from the empty one."""

    expected_bin: int
    detected_bin: int
    baseline: float  # empty-tee static power at the detected bin
    occupied: float  # ball-on-tee static power at the detected bin
    search_bins: tuple[int, int]  # inclusive local-bin range searched

    @property
    def ratio(self) -> float:
        """Occupied over baseline at the detected bin; baseline floored at 1."""
        return self.occupied / max(self.baseline, 1.0)

    @property
    def delta(self) -> float:
        return self.occupied - self.baseline

    @property
    def offset_bins(self) -> int:
        """Detected minus expected; nonzero every time means the range conversion is off."""
        return self.detected_bin - self.expected_bin


def detect_ball(
    baseline: dict[int, float],
    occupied: dict[int, float],
    expected_bin: int,
    *,
    half_width: int = DEFAULT_SEARCH_HALF_WIDTH,
) -> BallDetection:
    """The bin near ``expected_bin`` whose static power grew most when the ball was placed.

    The search is a window, not the single computed bin: the tee range is
    measured by hand and the range conversion may be off by a bin or two.
    """
    low, high = expected_bin - half_width, expected_bin + half_width
    candidates = [b for b in sorted(baseline) if low <= b <= high and b in occupied]
    if not candidates:
        raise ValueError(f"no scanned bins within {low}..{high} of the expected tee bin")
    detected = max(candidates, key=lambda b: occupied[b] - baseline[b])
    return BallDetection(
        expected_bin=expected_bin,
        detected_bin=detected,
        baseline=baseline[detected],
        occupied=occupied[detected],
        search_bins=(candidates[0], candidates[-1]),
    )


def local_bin_range_m(local_bin: int, window_start: int, fft_size: int = 128) -> float:
    """Range of a capture-local bin, the inverse of ``tee_local_bin``."""
    return (window_start + local_bin) * (RANGE_SPAN_M / fft_size)


__all__ = [
    "DEFAULT_MIN_RATIO",
    "DEFAULT_SCANS",
    "DEFAULT_SEARCH_HALF_WIDTH",
    "BallDetection",
    "TeeScan",
    "average_scans",
    "detect_ball",
    "local_bin_range_m",
    "parse_tee_scan",
]
