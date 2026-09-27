"""Stationary-ball detection from the firmware's static range power.

The self-trigger works from the MTI residual, which removes a ball sitting
on the tee entirely, so it can never say whether the radar sees the ball at
all. ``ball scan`` reports the raw static power per global range bin; this
module parses it, averages repeated scans, and compares an empty tee against
an occupied one to find where the ball actually is relative to where the
configured tee range says it should be. ``ball status`` is the firmware's
own appearance detector (a new compact reflector against a learned
background); it is parsed here too, and the ball's range is classified
against the supported setup envelope.
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


def bin_range_m(global_bin: int, fft_size: int = 128) -> float:
    """Range of a global range-FFT bin, the inverse of ``tee_global_bin``."""
    return global_bin * (RANGE_SPAN_M / fft_size)


@dataclass(frozen=True)
class BallStatus:
    """The firmware's placement detector, from ``ball status`` (two lines) or ``stats`` (one)."""

    state: str  # off, building, waiting, candidate, locked
    follow: bool
    bin: int | None  # global bin while locked
    ratio: float
    confidence: float
    locks: int
    releases: int
    reason: str
    window: tuple[int, int]  # (first global bin, bins) the detector covers
    centroid: float | None = None  # from the balldbg line, sub-bin global
    width: int | None = None
    persistence: float | None = None

    @property
    def locked(self) -> bool:
        return self.state == "locked" and self.bin is not None

    @property
    def range_m(self) -> float | None:
        """Range of the centroid when known, else of the locked bin."""
        if self.centroid is not None and self.locked:
            return self.centroid * (RANGE_SPAN_M / 128)
        return bin_range_m(self.bin) if self.bin is not None else None


_KEY_VALUE = re.compile(r"(\S+?)=(\S+)")


def parse_ball_status(text: str) -> BallStatus:
    """Parse ``ball status`` or a ``stats`` reply; raises ValueError without a ball line."""
    status: dict[str, str] = {}
    debug: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ball state="):
            status = dict(_KEY_VALUE.findall(line[len("ball ") :]))
        elif line.startswith("balldbg "):
            debug = dict(_KEY_VALUE.findall(line[len("balldbg ") :]))
    if "state" not in status:
        raise ValueError(f"no ball status line in {text.strip()[:80]!r}")
    state = status["state"]
    first, _, count = status.get("window", "0+0").partition("+")
    seen, _, total = debug.get("persistence", "0/0").partition("/")
    return BallStatus(
        state=state,
        follow=status.get("follow") == "1",
        bin=int(status["bin"]) if state == "locked" else None,
        ratio=float(status.get("ratio", 0)),
        confidence=float(status.get("confidence", 0)),
        locks=int(status.get("locks", 0)),
        releases=int(status.get("releases", 0)),
        reason=status.get("reason", "?"),
        window=(int(first or 0), int(count or 0)),
        centroid=float(debug["centroid"]) if "centroid" in debug and state == "locked" else None,
        width=int(debug["width"]) if "width" in debug else None,
        persistence=(int(seen) / int(total)) if total and int(total) else None,
    )


# Supported tee ranges, metres from the radar. Values to validate on real
# rigs, not final: the ten captures that prompted this put the club at
# 2.2-2.4 m with a tee configured at 1.575 m.
SETUP_TOO_CLOSE_M = 1.30
SETUP_CLOSE_M = 1.45
SETUP_FAR_M = 1.75
SETUP_TOO_FAR_M = 2.00
SETUP_IDEAL_M = 1.60  # the middle of the ideal band, for the "move by" advice


@dataclass(frozen=True)
class SetupAdvice:
    label: str  # too-close, close, ideal, far, too-far
    range_m: float
    move_m: float  # positive: move OpenFlight closer to the ball; negative: back

    @property
    def ok(self) -> bool:
        return self.label in ("close", "ideal", "far")

    @property
    def message(self) -> str:
        if self.label == "ideal":
            return "Ready"
        direction = "closer" if self.move_m > 0 else "back"
        distance_cm = abs(self.move_m) * 100.0
        if self.label in ("close", "far"):
            return (
                f"Usable; move OpenFlight about {distance_cm:.0f} cm {direction} for best results"
            )
        return f"Move OpenFlight about {distance_cm:.0f} cm {direction}"


def classify_setup(range_m: float) -> SetupAdvice:
    """Where the detected ball range sits in the supported envelope, and how to fix it."""
    if range_m < SETUP_TOO_CLOSE_M:
        label = "too-close"
    elif range_m < SETUP_CLOSE_M:
        label = "close"
    elif range_m <= SETUP_FAR_M:
        label = "ideal"
    elif range_m <= SETUP_TOO_FAR_M:
        label = "far"
    else:
        label = "too-far"
    return SetupAdvice(
        label=label, range_m=range_m, move_m=range_m - SETUP_IDEAL_M if label != "ideal" else 0.0
    )


__all__ = [
    "DEFAULT_MIN_RATIO",
    "DEFAULT_SCANS",
    "DEFAULT_SEARCH_HALF_WIDTH",
    "BallDetection",
    "BallStatus",
    "SetupAdvice",
    "TeeScan",
    "average_scans",
    "bin_range_m",
    "classify_setup",
    "detect_ball",
    "parse_ball_status",
    "parse_tee_scan",
]
