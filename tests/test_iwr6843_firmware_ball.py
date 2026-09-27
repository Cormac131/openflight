"""Behavioural tests for the IWR6843 ball-placement detector, firmware/iwr6843/l3_ball.c.

Built with the host C compiler and driven through ctypes with synthetic static
power profiles: a fixed background with clutter, then a ball appearing as a
compact rise at one bin, then leaving. Bins are global range-FFT bins.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
from pathlib import Path

import pytest

FIRMWARE_DIR = Path(__file__).parents[1] / "firmware" / "iwr6843"
SOURCE = FIRMWARE_DIR / "l3_ball.c"

MAX_BINS = 64
OFF, BUILDING, WAITING, CANDIDATE, LOCKED = range(5)
WINDOW_START = 20
COUNT = 53


class Cfg(ctypes.Structure):
    _fields_ = [
        ("enabled", ctypes.c_uint8),
        ("follow", ctypes.c_uint8),
        ("minRatio", ctypes.c_float),
        ("buildUpdates", ctypes.c_uint32),
        ("stableUpdates", ctypes.c_uint32),
        ("goneFraction", ctypes.c_float),
        ("goneUpdates", ctypes.c_uint32),
    ]


REASONS = ["none", "no_delta", "too_wide", "unstable", "gone"]


class Ball(ctypes.Structure):
    _fields_ = [
        ("cfg", Cfg),
        ("state", ctypes.c_uint8),
        ("reason", ctypes.c_uint8),
        ("updates", ctypes.c_uint32),
        ("windowStartBin", ctypes.c_uint32),
        ("count", ctypes.c_uint32),
        ("background", ctypes.c_float * MAX_BINS),
        ("current", ctypes.c_float * MAX_BINS),
        ("candidateBin", ctypes.c_uint32),
        ("candidateAge", ctypes.c_uint32),
        ("centroid", ctypes.c_float),
        ("width", ctypes.c_uint32),
        ("ballBin", ctypes.c_uint32),
        ("ballCentroid", ctypes.c_float),
        ("ballDelta", ctypes.c_float),
        ("ballBackground", ctypes.c_float),
        ("ballAge", ctypes.c_uint32),
        ("goneAge", ctypes.c_uint32),
        ("history", ctypes.c_uint64),
        ("locks", ctypes.c_uint32),
        ("releases", ctypes.c_uint32),
        ("reasons", ctypes.c_uint32 * len(REASONS)),
    ]


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None:
        pytest.skip("no C compiler for the firmware ball detector")
    out = tmp_path_factory.mktemp("l3_ball") / "l3_ball.so"
    subprocess.run(
        [
            compiler,
            "-std=c99",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-shared",
            "-fPIC",
            "-O1",
            "-o",
            str(out),
            str(SOURCE),
        ],
        check=True,
        cwd=FIRMWARE_DIR,
    )
    library = ctypes.CDLL(str(out))
    library.l3_ball_cfg_defaults.argtypes = [ctypes.POINTER(Cfg)]
    library.l3_ball_cfg_check.argtypes = [ctypes.POINTER(Cfg)]
    library.l3_ball_cfg_check.restype = ctypes.c_int32
    library.l3_ball_init.argtypes = [ctypes.POINTER(Ball), ctypes.POINTER(Cfg)]
    library.l3_ball_update.argtypes = [
        ctypes.POINTER(Ball),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_uint32,
    ]
    library.l3_ball_update.restype = ctypes.c_uint8
    library.l3_ball_locked.argtypes = [ctypes.POINTER(Ball), ctypes.POINTER(ctypes.c_uint32)]
    library.l3_ball_locked.restype = ctypes.c_int32
    library.l3_ball_ratio.argtypes = [ctypes.POINTER(Ball)]
    library.l3_ball_ratio.restype = ctypes.c_float
    library.l3_ball_state_name.argtypes = [ctypes.c_uint8]
    library.l3_ball_state_name.restype = ctypes.c_char_p
    library.l3_ball_reason_name.argtypes = [ctypes.c_uint8]
    library.l3_ball_reason_name.restype = ctypes.c_char_p
    library.l3_ball_persistence.argtypes = [ctypes.POINTER(Ball)]
    library.l3_ball_persistence.restype = ctypes.c_float
    library.l3_ball_confidence.argtypes = [ctypes.POINTER(Ball)]
    library.l3_ball_confidence.restype = ctypes.c_float
    for name in ("l3_ball_format_status", "l3_ball_format_debug"):
        getattr(library, name).argtypes = [ctypes.POINTER(Ball), ctypes.c_char_p, ctypes.c_uint32]
        getattr(library, name).restype = ctypes.c_int32
    return library


# The lane: flat 1e6 with a strong static reflector (furniture) at global bin 44.
BACKGROUND = {b: 1.0e6 for b in range(WINDOW_START, WINDOW_START + COUNT)}
BACKGROUND[44] = 1.74e9
BALL_BIN = 48
BALL_POWER = 6.0e6  # bin 48 goes from 1e6 to 6e6: ratio 5


class Lane:
    def __init__(self, lib, **overrides):
        self.lib = lib
        self.cfg = Cfg()
        lib.l3_ball_cfg_defaults(ctypes.byref(self.cfg))
        self.cfg.enabled = 1
        for name, value in overrides.items():
            setattr(self.cfg, name, value)
        assert lib.l3_ball_cfg_check(ctypes.byref(self.cfg)) == 0
        self.ball = Ball()
        lib.l3_ball_init(ctypes.byref(self.ball), ctypes.byref(self.cfg))

    def update(
        self,
        profile: dict[int, float] | None = None,
        extra: dict[int, float] | None = None,
        *,
        start: int = WINDOW_START,
        count: int = COUNT,
    ) -> int:
        profile = dict(BACKGROUND if profile is None else profile)
        profile.update(extra or {})
        power = (ctypes.c_float * count)()
        for i in range(count):
            power[i] = profile.get(start + i, 1.0e6)
        return self.lib.l3_ball_update(ctypes.byref(self.ball), start, power, count)

    def run(self, n: int, **kwargs) -> int:
        state = self.ball.state
        for _ in range(n):
            state = self.update(**kwargs)
        return state

    def locked_bin(self) -> int | None:
        out = ctypes.c_uint32()
        return (
            out.value
            if self.lib.l3_ball_locked(ctypes.byref(self.ball), ctypes.byref(out))
            else None
        )

    def status(self) -> str:
        buf = ctypes.create_string_buffer(256)
        self.lib.l3_ball_format_status(ctypes.byref(self.ball), buf, len(buf))
        return buf.value.decode()

    def debug(self) -> str:
        buf = ctypes.create_string_buffer(256)
        self.lib.l3_ball_format_debug(ctypes.byref(self.ball), buf, len(buf))
        return buf.value.decode()

    def reason(self) -> str:
        return self.lib.l3_ball_reason_name(self.ball.reason).decode()


def test_defaults_pass_the_check_and_disabled_stays_off(lib):
    lane = Lane(lib)
    assert (lane.cfg.minRatio, lane.cfg.buildUpdates, lane.cfg.stableUpdates) == (1.0, 64, 12)
    assert lane.cfg.goneUpdates == 20, "removal needs longer than acquisition: no chatter"
    assert lane.ball.state == BUILDING
    off = Lane(lib, enabled=0)
    assert off.run(100) == OFF


@pytest.mark.parametrize(
    "overrides",
    [
        {"minRatio": 0.0},
        {"buildUpdates": 0},
        {"stableUpdates": 0},
        {"goneUpdates": 0},
        {"goneFraction": 1.0},
    ],
)
def test_config_the_detector_cannot_run_is_rejected(lib, overrides):
    cfg = Cfg()
    lib.l3_ball_cfg_defaults(ctypes.byref(cfg))
    for name, value in overrides.items():
        setattr(cfg, name, value)
    assert lib.l3_ball_cfg_check(ctypes.byref(cfg)) != 0


def test_background_builds_then_waits_with_no_ball(lib):
    lane = Lane(lib)
    assert lane.run(63) == BUILDING
    assert lane.run(1) == WAITING
    assert lane.run(200) == WAITING, "clutter that was always there is never a ball"
    assert lane.ball.background[44 - WINDOW_START] == pytest.approx(1.74e9, rel=0.01)


def test_a_placed_ball_locks_at_its_bin_not_at_the_strongest_reflector(lib):
    lane = Lane(lib)
    lane.run(64)
    state = lane.run(20, extra={BALL_BIN: BALL_POWER})
    assert state == LOCKED
    assert lane.locked_bin() == BALL_BIN, "bin 44 (1.74e9) was there before; bin 48 is new"
    assert lane.lib.l3_ball_ratio(ctypes.byref(lane.ball)) == pytest.approx(5.0, rel=0.05)
    assert "state=locked" in lane.status() and "bin=48" in lane.status()


def test_lock_needs_the_candidate_to_stay_put(lib):
    lane = Lane(lib, stableUpdates=8)
    lane.run(64)
    # A hand passing: two updates at one bin, then gone. The fast profile
    # smooths at 1/4 per update, so the rise takes three more updates to
    # fall under the ratio; the candidate age never reaches eight.
    lane.run(2, extra={40: BALL_POWER})
    assert lane.ball.state == CANDIDATE
    assert lane.run(3) == WAITING
    assert lane.locked_bin() is None
    assert lane.reason() == "unstable"


def test_a_ball_straddling_two_bins_still_locks(lib):
    lane = Lane(lib)
    lane.run(64)
    states = [lane.update(extra={BALL_BIN: 4.0e6, BALL_BIN + 1: 3.5e6}) for _ in range(20)]
    assert states[-1] == LOCKED
    assert lane.locked_bin() in (BALL_BIN, BALL_BIN + 1)


def test_a_person_stepping_in_is_not_compact_enough_to_be_a_ball(lib):
    lane = Lane(lib)
    lane.run(64)
    body = {b: 5.0e6 for b in range(30, 40)}  # ten bins rise together
    assert lane.run(30, extra=body) == WAITING
    assert lane.locked_bin() is None


def test_the_locked_ball_is_not_learned_into_the_background(lib):
    lane = Lane(lib)
    lane.run(64)
    lane.run(20, extra={BALL_BIN: BALL_POWER})
    assert lane.run(2000, extra={BALL_BIN: BALL_POWER}) == LOCKED
    assert lane.ball.background[BALL_BIN - WINDOW_START] == pytest.approx(1.0e6, rel=0.05)
    assert lane.ball.ballAge >= 2000


def test_removing_the_ball_releases_and_the_next_ball_locks_again(lib):
    lane = Lane(lib)
    lane.run(64)
    lane.run(20, extra={BALL_BIN: BALL_POWER})
    assert lane.ball.locks == 1
    assert lane.run(19) == LOCKED, "one bad frame, or nineteen, does not release the ball"
    assert lane.reason() == "gone"
    # The fast profile takes ~5 updates to fall under the gone fraction, then
    # twenty consecutive gone updates release the ball.
    assert lane.run(8) == WAITING
    assert lane.ball.releases == 1
    assert lane.run(20, extra={BALL_BIN + 1: BALL_POWER}) == LOCKED
    assert lane.locked_bin() == BALL_BIN + 1
    assert lane.ball.locks == 2


def test_background_keeps_learning_slow_drift_elsewhere_while_locked(lib):
    """A bin that warms by a third is drift and follows within seconds; a bin
    that triples holds something new and is learned at the rise rate instead."""
    lane = Lane(lib)
    lane.run(64)
    lane.run(20, extra={BALL_BIN: BALL_POWER})
    drifted = dict(BACKGROUND)
    drifted[30] = 1.3e6
    drifted[31] = 3.0e6
    lane.run(3000, profile=drifted, extra={BALL_BIN: BALL_POWER})
    assert lane.ball.background[30 - WINDOW_START] == pytest.approx(1.3e6, rel=0.05)
    assert 1.2e6 < lane.ball.background[31 - WINDOW_START] < 2.0e6
    assert lane.ball.state == LOCKED


def test_a_new_window_restarts_the_background(lib):
    lane = Lane(lib)
    lane.run(64)
    assert lane.ball.state == WAITING
    assert lane.update(start=47) == BUILDING
    assert lane.ball.windowStartBin == 47 and lane.ball.updates == 1


def test_status_line_reads_without_float_printf(lib):
    lane = Lane(lib, follow=1)
    lane.run(64)
    lane.run(20, extra={BALL_BIN: BALL_POWER})
    line = lane.status()
    assert line.startswith("ball state=locked follow=1 bin=48 ratio=")
    assert " confidence=" in line and " locks=1 releases=0 reason=none " in line
    assert line.endswith(f"window={WINDOW_START}+{COUNT}")
    for state, name in enumerate(["off", "building", "waiting", "candidate", "locked"]):
        assert lane.lib.l3_ball_state_name(state).decode() == name
    for code, name in enumerate(REASONS):
        assert lane.lib.l3_ball_reason_name(code).decode() == name


def test_no_rise_is_reported_as_no_delta(lib):
    lane = Lane(lib)
    lane.run(70)
    assert lane.reason() == "no_delta"
    assert lane.ball.reasons[REASONS.index("no_delta")] >= 6


def test_a_wide_rise_is_reported_as_too_wide(lib):
    lane = Lane(lib)
    lane.run(64)
    body = {b: 5.0e6 for b in range(30, 40)}
    lane.run(10, extra=body)
    assert lane.reason() == "too_wide"
    assert lane.ball.width >= 3


def test_centroid_sits_between_two_bins_the_ball_straddles(lib):
    lane = Lane(lib)
    lane.run(64)
    # The neighbour rises 1.6x: clearly over the half-of-the-peak cluster
    # test (1.5x would sit on its boundary, decided by learning round-off).
    lane.run(30, extra={BALL_BIN: 4.0e6, BALL_BIN + 1: 2.6e6})
    assert lane.ball.state == LOCKED
    assert lane.ball.width == 2
    # deltas 3e6 and 1.6e6: centroid = (48*3 + 49*1.6)/4.6 = 48.35
    assert lane.ball.centroid == pytest.approx(48.35, abs=0.05)
    assert "centroid=48.3" in lane.debug() and "width=2" in lane.debug()


def test_persistence_and_confidence_grow_while_the_ball_sits_still(lib):
    lane = Lane(lib)
    lane.run(64)
    lane.run(13, extra={BALL_BIN: BALL_POWER})
    assert lane.ball.state == LOCKED
    early = lane.lib.l3_ball_confidence(ctypes.byref(lane.ball))
    lane.run(60, extra={BALL_BIN: BALL_POWER})
    late = lane.lib.l3_ball_confidence(ctypes.byref(lane.ball))
    assert lane.lib.l3_ball_persistence(ctypes.byref(lane.ball)) == pytest.approx(1.0)
    assert late > early
    assert late == pytest.approx(1.0, abs=0.02), "ratio 5 >= 4, one bin, always seen, no drift"
    assert "persistence=50/50" in lane.debug()


def test_confidence_falls_when_the_ball_return_drifts_in_range(lib):
    lane = Lane(lib)
    lane.run(64)
    lane.run(40, extra={BALL_BIN: BALL_POWER})
    steady = lane.lib.l3_ball_confidence(ctypes.byref(lane.ball))
    # The return leans into the next bin: the centroid moves ~0.4 bins.
    lane.run(40, extra={BALL_BIN: BALL_POWER, BALL_BIN + 1: 4.0e6})
    assert lane.ball.state == LOCKED and lane.locked_bin() == BALL_BIN, "the bin stays frozen"
    drifted = lane.lib.l3_ball_confidence(ctypes.byref(lane.ball))
    assert drifted < steady


# --- hardware run 2: a ball on the tee left the detector "waiting" ------------


def test_a_hand_lingering_over_the_placed_ball_does_not_teach_the_background_the_ball(lib):
    """The hand placing the ball is a wide rise. While that was rejected as too
    wide, the background learned everything under it, the ball included, at the
    quiet-lane rate, so once the hand withdrew nothing was left to lock on."""
    lane = Lane(lib)
    lane.run(64)
    hand = {b: 5.0e6 for b in range(BALL_BIN - 3, BALL_BIN + 3)}
    hand[BALL_BIN] = BALL_POWER
    assert lane.run(1500, extra=hand) == WAITING  # ~9 s of hand at 167 updates/s
    assert lane.reason() == "too_wide"
    assert lane.run(20, extra={BALL_BIN: BALL_POWER}) == LOCKED
    assert lane.locked_bin() == BALL_BIN
    assert lane.lib.l3_ball_ratio(ctypes.byref(lane.ball)) > 2.0


def test_a_ball_beside_a_standing_person_still_locks(lib):
    """The strongest rise is the golfer, ten bins wide; the compact rise beside it is the ball."""
    lane = Lane(lib)
    lane.run(64)
    body = {b: 2.0e7 for b in range(30, 40)}
    assert lane.run(20, extra={**body, BALL_BIN: BALL_POWER}) == LOCKED
    assert lane.locked_bin() == BALL_BIN
    assert lane.ball.reasons[REASONS.index("too_wide")] > 0, "the body was seen and set aside"


def test_a_rise_that_never_locks_is_learned_slowly_rather_than_never(lib):
    """A moved bag must not mask the lane for good: a rise that is not a ball
    still becomes background, over minutes rather than a second."""
    lane = Lane(lib)
    lane.run(64)
    body = {b: 5.0e6 for b in range(30, 40)}
    lane.run(2000, extra=body)  # ~12 s
    assert lane.ball.background[35 - WINDOW_START] < 2.5e6, (
        "a hand's stay leaves it mostly unlearned"
    )
    lane.run(40000, extra=body)  # ~4 minutes
    assert lane.ball.background[35 - WINDOW_START] == pytest.approx(5.0e6, rel=0.05)
