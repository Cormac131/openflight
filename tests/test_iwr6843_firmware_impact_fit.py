"""Tests for impact from the tracks either side of the tee band,
firmware/iwr6843/l3_impact_fit.c.

Scene used throughout: ball at rest at 2.20 m, impact at t = 30 000 us,
band +/- 6 bins (+/- 0.281 m). Club in at 30 m/s, club out at 25 m/s, ball
out at 60 m/s; every point lies outside the band.
"""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw

BIN_M = 6.0 / 128
BALL_M = 2.20
IMPACT_US = 30_000.0
CLUB_IN, CLUB_OUT, BALL_OUT = 0, 1, 2
WHY = {name: i for i, name in enumerate(fw.FIT_WHY_NAMES)}
VERDICT = {name: i for i, name in enumerate(fw.FIT_VERDICT_NAMES)}


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def cfg(lib, **overrides) -> fw.ImpactFitCfg:
    c = fw.ImpactFitCfg()
    lib.l3_impact_fit_cfg_defaults(ctypes.byref(c))
    for name, value in overrides.items():
        setattr(c, name, value)
    return c


def line(speed_mps: float, times_us, *, noise_m=()) -> list[tuple[float, float]]:
    """(t_us, r_m) on the line through (IMPACT_US, BALL_M) at speed_mps."""
    noise = list(noise_m) + [0.0] * len(times_us)
    return [(t, BALL_M + speed_mps * (t - IMPACT_US) * 1e-6 + noise[i]) for i, t in enumerate(times_us)]


def point_list(samples) -> fw.FitList:
    arr = (fw.TrackPoint * max(1, len(samples)))()
    for i, (t, r) in enumerate(samples):
        arr[i].frame = i
        arr[i].timestampUs = int(round(t))
        arr[i].rangeM = r
        arr[i].rangeBin = r / BIN_M
    out = fw.FitList(ctypes.cast(arr, ctypes.POINTER(fw.TrackPoint)), len(samples))
    out.keep = arr  # keep the array alive as long as the list
    return out


def estimate(lib, which, samples, **overrides) -> fw.FitEstimate:
    lst = point_list(samples)
    out = fw.FitEstimate()
    lib.l3_impact_fit_track(
        ctypes.byref(cfg(lib, **overrides)),
        which,
        fw.fit_reader(lib),
        ctypes.byref(lst),
        len(samples),
        BALL_M,
        ctypes.byref(out),
    )
    return out


CLUB_IN_T = (9_000, 12_000, 15_000, 18_000)
CLUB_OUT_T = (42_000, 45_000, 48_000, 51_000)
BALL_OUT_T = (36_000, 39_000, 42_000, 45_000)


def test_defaults_are_the_specs(lib):
    c = cfg(lib)
    assert c.bandBins == 6.0 and c.fitPoints == 4 and c.minPoints == 3
    assert (c.clubMinMps, c.clubMaxMps, c.clubOutMaxRatio) == (10.0, 70.0, pytest.approx(1.10))
    assert (c.ballMinMps, c.ballMaxMps) == (15.0, 90.0)
    assert (c.gateSigmas, c.minSigmaUs) == (3.0, 500.0)
    assert c.binWidthM == pytest.approx(BIN_M)


@pytest.mark.parametrize(
    "which, speed, times",
    [(CLUB_IN, 30.0, CLUB_IN_T), (CLUB_OUT, 25.0, CLUB_OUT_T), (BALL_OUT, 60.0, BALL_OUT_T)],
)
def test_clean_line_crosses_the_ball_at_impact(lib, which, speed, times):
    e = estimate(lib, which, line(speed, times))
    assert e.why == WHY["ok"]
    assert e.points == 4
    assert e.speedMps == pytest.approx(speed, rel=1e-4)
    assert e.timeUs == pytest.approx(IMPACT_US, abs=2.0)


def test_sigma_floor_is_a_bin_of_quantisation_over_speed(lib):
    e = estimate(lib, CLUB_IN, line(30.0, CLUB_IN_T))
    floor_us = BIN_M / 12**0.5 / 30.0 * 1e6
    assert e.sigmaUs == pytest.approx(floor_us, rel=1e-3)


def test_sigma_grows_with_extrapolation_distance(lib):
    noise = (0.01, -0.01, 0.012, -0.008)
    near = estimate(lib, BALL_OUT, line(60.0, BALL_OUT_T, noise_m=noise))
    far_t = tuple(t + 15_000 for t in BALL_OUT_T)
    far = estimate(lib, BALL_OUT, line(60.0, far_t, noise_m=noise))
    assert near.why == far.why == WHY["ok"]
    assert far.sigmaUs > near.sigmaUs


def test_club_in_uses_its_last_k_points_and_outs_their_first_k(lib):
    # A stray early club-in point and a stray late ball point, both far off the line.
    club = [(0.0, 0.5)] + line(30.0, CLUB_IN_T)
    ball = line(60.0, BALL_OUT_T) + [(60_000.0, 9.0)]
    e_in = estimate(lib, CLUB_IN, club)
    e_out = estimate(lib, BALL_OUT, ball)
    assert e_in.timeUs == pytest.approx(IMPACT_US, abs=2.0)
    assert e_out.timeUs == pytest.approx(IMPACT_US, abs=2.0)


def test_strided_timestamps_are_honoured(lib):
    e = estimate(lib, BALL_OUT, line(60.0, (36_000, 42_000, 48_000, 54_000)))
    assert e.timeUs == pytest.approx(IMPACT_US, abs=2.0)


def test_no_points_is_missing(lib):
    assert estimate(lib, CLUB_IN, []).why == WHY["missing"]


def test_two_points_are_too_few(lib):
    e = estimate(lib, CLUB_IN, line(30.0, CLUB_IN_T[:2]))
    assert e.why == WHY["few_points"] and e.points == 2


def test_points_that_do_not_spread_in_time_are_nonfinite(lib):
    same = [(12_000.0, 1.6), (12_000.0, 1.7), (12_000.0, 1.8)]
    assert estimate(lib, CLUB_IN, same).why == WHY["nonfinite"]


@pytest.mark.parametrize("which, times", [(CLUB_IN, CLUB_IN_T), (CLUB_OUT, CLUB_OUT_T), (BALL_OUT, BALL_OUT_T)])
def test_moving_toward_the_radar_is_the_wrong_direction(lib, which, times):
    assert estimate(lib, which, line(-20.0, times)).why == WHY["wrong_direction"]


@pytest.mark.parametrize(
    "which, speed, times",
    [
        (CLUB_IN, 9.0, CLUB_IN_T),
        (CLUB_IN, 71.0, CLUB_IN_T),
        (CLUB_OUT, 71.0, CLUB_OUT_T),
        (BALL_OUT, 14.0, BALL_OUT_T),
        (BALL_OUT, 91.0, BALL_OUT_T),
    ],
)
def test_speeds_outside_the_bounds_are_rejected(lib, which, speed, times):
    assert estimate(lib, which, line(speed, times)).why == WHY["speed_bounds"]


def test_slow_club_out_is_accepted(lib):
    # After impact the club only slows; there is no lower bound but "moving away".
    assert estimate(lib, CLUB_OUT, line(3.0, CLUB_OUT_T)).why == WHY["ok"]


def test_span_after_reads_only_points_appended_after_a_frame(lib):
    track_cfg = fw.TrackCfg()
    lib.l3_track_cfg_defaults(ctypes.byref(track_cfg))
    track = fw.ClubTrack()
    lib.l3_track_init(ctypes.byref(track), ctypes.byref(track_cfg))
    for frame, (t, r) in enumerate(line(30.0, (3_000, 6_000, 9_000, 12_000, 15_000))):
        p = fw.TrackPoint()
        p.frame, p.timestampUs, p.rangeM, p.rangeBin = frame, int(t), r, r / BIN_M
        lib.l3_track_append_point(ctypes.byref(track), ctypes.byref(p))

    span = fw.FitSpan()
    lib.l3_fit_span_after(ctypes.byref(track), 2, ctypes.byref(span))

    assert (span.first, span.count) == (3, 2)
    out = fw.TrackPoint()
    assert lib.l3_fit_span_point(ctypes.byref(span), 0, ctypes.byref(out)) == 1
    assert out.frame == 3
    assert lib.l3_fit_span_point(ctypes.byref(span), 2, ctypes.byref(out)) == 0
