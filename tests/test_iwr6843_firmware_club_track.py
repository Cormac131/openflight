"""Tests for the IWR6843 club track, firmware/iwr6843/l3_club_track.c.

Built with the host C compiler (openflight.iwr6843.firmware_host, which
also holds the ctypes mirrors) and driven through ctypes with synthetic
target lists: a clubhead closing on the ball at a steady bins-per-frame rate,
decoys the association must ignore, gaps to coast over, and a range-over-time
fit that yields the club's radial speed. Bins are global range-FFT bins.
"""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843.firmware_host import (
    TRACK_POINTS as POINTS,
    TRACK_WHY_NAMES as WHY,
    ClubTrack as Track,
    TargetObs as Target,
    TrackCfg as Cfg,
    TrackPoint as Point,
    build_firmware_library,
    host_compiler,
)

BIN_M = 6.0 / 128
FRAME_US = 3000


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def target(
    frame: int,
    range_bin: float,
    *,
    confidence: float = 0.9,
    doppler: float = 0.0,
    energy: float = 5000.0,
) -> Target:
    t = Target()
    t.frame = frame
    t.timestampUs = frame * FRAME_US
    t.peakBin = int(round(range_bin))
    t.rangeBin = range_bin
    t.energy = energy
    t.peak = energy / 4
    t.stat = t.peak
    t.snr = 20.0
    t.coherence = 0.9
    t.dopplerAliasMps = doppler
    t.confidence = confidence
    return t


class Tracker:
    def __init__(self, lib, **overrides):
        self.lib = lib
        self.cfg = Cfg()
        lib.l3_track_cfg_defaults(ctypes.byref(self.cfg))
        for name, value in overrides.items():
            setattr(self.cfg, name, value)
        self.track = Track()
        lib.l3_track_init(ctypes.byref(self.track), ctypes.byref(self.cfg))

    def update(self, frame: int, targets: list[Target]) -> bool:
        arr = (Target * max(1, len(targets)))(*targets)
        return bool(
            self.lib.l3_track_update(
                ctypes.byref(self.track), arr, len(targets), frame, frame * FRAME_US
            )
        )

    def points(self) -> list[Point]:
        out = []
        for i in range(self.track.count):
            p = Point()
            assert self.lib.l3_track_point(ctypes.byref(self.track), i, ctypes.byref(p)) == 1
            out.append(p)
        return out

    def why(self) -> str:
        return self.lib.l3_track_why_name(self.track.why).decode()

    def fit(self, max_points: int = 8):
        slope = ctypes.c_float()
        residual = ctypes.c_float()
        used = self.lib.l3_track_fit(
            ctypes.byref(self.track), max_points, ctypes.byref(slope), ctypes.byref(residual)
        )
        return used, slope.value, residual.value

    def status(self, dest: int = 48) -> str:
        buf = ctypes.create_string_buffer(256)
        self.lib.l3_track_format_status(ctypes.byref(self.track), dest, buf, len(buf))
        return buf.value.decode()


def test_defaults_describe_the_wide_profile(lib):
    tr = Tracker(lib)
    assert tr.cfg.binWidthM == pytest.approx(BIN_M)
    assert (tr.cfg.gateBins, tr.cfg.maxMisses) == (3.0, 2)
    assert tr.cfg.velocitySpanMps == pytest.approx(2 * 0.00484 / (4 * 135e-6), rel=0.01)


def test_a_steady_approach_becomes_one_continuous_track(lib):
    """Bins 22, 25, 27, 30, 33 ... 48: the trajectory the ten captures showed."""
    tr = Tracker(lib)
    path = [22, 25, 27, 30, 33, 35, 38, 40, 43, 45, 46, 47, 48]
    for frame, b in enumerate(path, start=1):
        assert tr.update(frame, [target(frame, float(b))]) is True
    assert tr.track.active == 1 and tr.track.count == len(path)
    assert tr.why() == "associated"
    assert [round(p.rangeBin) for p in tr.points()] == path
    assert tr.track.counters[WHY.index("acquired")] == 1, "acquired once, never re-acquired"


def test_association_follows_the_prediction_not_the_strongest_return(lib):
    """The hands light up 12 bins behind the club, stronger and more confident."""
    tr = Tracker(lib)
    for frame, b in enumerate([20, 22, 24], start=1):
        tr.update(frame, [target(frame, float(b))])
    decoy = target(4, 14.0, confidence=1.0, energy=50000.0)
    club = target(4, 26.0, confidence=0.6)
    assert tr.update(4, [decoy, club]) is True
    assert tr.points()[-1].rangeBin == pytest.approx(26.0)
    assert tr.why() == "associated"


def test_inside_the_gate_range_error_doppler_continuity_and_quality_all_count(lib):
    tr = Tracker(lib)
    for frame, b in enumerate([20, 22, 24], start=1):
        tr.update(frame, [target(frame, float(b), doppler=3.0)])
    # Two candidates half a bin either side of the prediction (26), so range
    # alone cannot separate them: one with a Doppler half a span away from
    # the track's, one with continuous Doppler and better quality.
    a = target(4, 25.5, confidence=0.5, doppler=-5.5)
    b = target(4, 26.5, confidence=0.9, doppler=3.1)
    tr.update(4, [a, b])
    assert tr.points()[-1].rangeBin == pytest.approx(26.5)
    # With Doppler and quality ignored the nearer-in-range candidate wins.
    tr = Tracker(lib, weightVelocity=0.0, weightQuality=0.0)
    for frame, b_ in enumerate([20, 22, 24], start=1):
        tr.update(frame, [target(frame, float(b_), doppler=3.0)])
    tr.update(4, [target(4, 25.6, confidence=0.5, doppler=-5.5), b])
    assert tr.points()[-1].rangeBin == pytest.approx(25.6)


def test_a_missing_frame_is_coasted_then_the_club_is_picked_up_where_predicted(lib):
    tr = Tracker(lib)
    for frame, b in enumerate([20, 22, 24], start=1):
        tr.update(frame, [target(frame, float(b))])
    assert tr.update(4, []) is False
    assert tr.why() == "coasted" and tr.track.active == 1
    assert tr.track.predictedBin == pytest.approx(26.0, abs=0.5)
    assert tr.update(5, [target(5, 28.0)]) is True
    assert tr.points()[-1].rangeBin == pytest.approx(28.0)
    assert tr.track.count == 4


def test_too_many_misses_drop_the_track_and_a_new_one_is_acquired(lib):
    tr = Tracker(lib)
    for frame, b in enumerate([20, 22, 24], start=1):
        tr.update(frame, [target(frame, float(b))])
    tr.update(4, [])
    tr.update(5, [])
    assert tr.update(6, []) is False
    assert tr.why() == "dropped" and tr.track.active == 0
    assert tr.update(7, [target(7, 40.0)]) is True
    assert tr.why() == "acquired"


def test_acquisition_needs_confidence_and_takes_the_most_confident(lib):
    tr = Tracker(lib)
    assert tr.update(1, [target(1, 30.0, confidence=0.1)]) is False
    assert tr.why() == "idle"
    assert tr.update(2, [target(2, 30.0, confidence=0.4), target(2, 40.0, confidence=0.8)]) is True
    assert tr.points()[0].rangeBin == pytest.approx(40.0)


def test_history_keeps_the_newest_32_points(lib):
    tr = Tracker(lib, gateBins=100.0)
    for frame in range(1, 41):
        tr.update(frame, [target(frame, 20.0 + 0.5 * frame)])
    assert tr.track.count == POINTS and tr.track.total == 40
    assert tr.points()[0].frame == 9 and tr.points()[-1].frame == 40


def test_fit_recovers_the_range_rate_as_speed(lib):
    """2.0 bins per 3 ms frame = 31.25 m/s radial at 46.875 mm bins."""
    tr = Tracker(lib)
    for frame in range(1, 9):
        tr.update(frame, [target(frame, 20.0 + 2.0 * frame)])
    used, slope, residual = tr.fit(8)
    assert used == 8
    assert slope == pytest.approx(2.0 / 3e-3, rel=0.01)
    assert residual == pytest.approx(0.0, abs=0.05)
    assert tr.lib.l3_track_speed_mps(ctypes.byref(tr.track), 8) == pytest.approx(31.25, rel=0.01)
    assert tr.points()[-1].radialVelocityMps == pytest.approx(31.25, rel=0.05)


def test_fit_needs_three_points_and_reports_scatter(lib):
    tr = Tracker(lib)
    tr.update(1, [target(1, 20.0)])
    tr.update(2, [target(2, 22.0)])
    assert tr.fit()[0] == 0 and tr.lib.l3_track_speed_mps(ctypes.byref(tr.track), 8) == 0.0
    tr.update(3, [target(3, 25.0)])  # off the line by a bin
    used, _slope, residual = tr.fit()
    assert used == 3 and residual > 0.2


def test_reset_forgets_the_track_but_keeps_configuration_and_counters(lib):
    tr = Tracker(lib)
    for frame, b in enumerate([20, 22, 24], start=1):
        tr.update(frame, [target(frame, float(b))])
    tr.lib.l3_track_reset(ctypes.byref(tr.track))
    assert tr.track.active == 0 and tr.track.count == 0
    assert tr.track.counters[WHY.index("acquired")] == 1
    assert tr.cfg.gateBins == 3.0


def test_status_and_point_lines_read_without_float_printf(lib):
    tr = Tracker(lib)
    for frame in range(1, 6):
        tr.update(frame, [target(frame, 30.0 + 2.0 * frame, doppler=-1.5)])
    status = tr.status(dest=48)
    assert status.startswith(
        "clubtrack active=1 why=associated count=5 total=5 misses=0 bin=40.00 dest=48 dist=8.00 "
    )
    assert " speed=31.25 fit=5 residual=0.00 " in status
    buf = ctypes.create_string_buffer(256)
    tr.lib.l3_track_format_point(ctypes.byref(tr.points()[-1]), 48, buf, len(buf))
    line = buf.value.decode()
    assert line.startswith(
        "p frame=5 t=15000 bin=40.00 dist=8.00 range=1.88 vr=31.25 vd=-1.50 coh=90 conf=0.90"
    )
    assert line.endswith("angles=none")
    for code, name in enumerate(WHY):
        assert tr.lib.l3_track_why_name(code).decode() == name
