"""Tests for the tee band, firmware/iwr6843/l3_band.c."""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def band(lib, centre: float, half: float) -> fw.Band:
    out = fw.Band()
    lib.l3_band_around(centre, half, ctypes.byref(out))
    return out


def targets(*bins: float):
    arr = (fw.TargetObs * len(bins))()
    for i, b in enumerate(bins):
        arr[i].rangeBin = b
        arr[i].snr = 100.0 - i  # strongest first, as l3_obs_extract ranks them
    return arr


def test_band_spans_half_width_either_side_of_the_centre(lib):
    b = band(lib, 47.0, 6.0)
    assert (b.valid, b.loBin, b.hiBin) == (1, 41.0, 53.0)


def test_zero_or_negative_half_width_disables_the_band(lib):
    for half in (0.0, -1.0):
        b = band(lib, 47.0, half)
        assert b.valid == 0
        assert lib.l3_band_contains(ctypes.byref(b), 47.0) == 0


def test_edges_are_inside(lib):
    b = band(lib, 47.0, 6.0)
    assert lib.l3_band_contains(ctypes.byref(b), 41.0) == 1
    assert lib.l3_band_contains(ctypes.byref(b), 53.0) == 1
    assert lib.l3_band_contains(ctypes.byref(b), 40.99) == 0
    assert lib.l3_band_contains(ctypes.byref(b), 53.01) == 0


def test_filter_removes_every_in_band_target_and_keeps_order(lib):
    b = band(lib, 47.0, 6.0)
    arr = targets(47.0, 30.0, 41.0, 60.0, 52.9, 35.5)

    kept = lib.l3_band_filter(ctypes.byref(b), arr, 6)

    assert kept == 3
    assert [arr[i].rangeBin for i in range(kept)] == [30.0, 60.0, 35.5]
    assert [arr[i].snr for i in range(kept)] == [99.0, 97.0, 95.0]


def test_filter_with_everything_in_band_keeps_nothing(lib):
    b = band(lib, 47.0, 6.0)
    arr = targets(44.0, 47.0, 50.0)
    assert lib.l3_band_filter(ctypes.byref(b), arr, 3) == 0


def test_disabled_band_filters_nothing(lib):
    b = band(lib, 47.0, 0.0)
    arr = targets(47.0, 48.0)
    assert lib.l3_band_filter(ctypes.byref(b), arr, 2) == 2


def test_keep_short_keeps_only_targets_short_of_the_band_in_order(lib):
    """Before impact the club approaches the ball: only returns short of the
    band can be it; the band and everything beyond it are dropped."""
    b = band(lib, 47.0, 6.0)
    arr = targets(47.0, 30.0, 41.0, 60.0, 40.5, 53.0, 35.5)

    kept = lib.l3_band_keep_short(ctypes.byref(b), arr, 7)

    assert kept == 3
    assert [arr[i].rangeBin for i in range(kept)] == [30.0, 40.5, 35.5]
    assert [arr[i].snr for i in range(kept)] == [99.0, 96.0, 94.0]


def test_keep_short_drops_the_low_edge_itself(lib):
    b = band(lib, 47.0, 6.0)
    arr = targets(41.0)
    assert lib.l3_band_keep_short(ctypes.byref(b), arr, 1) == 0


def test_keep_short_with_an_invalid_band_keeps_everything(lib):
    b = band(lib, 47.0, 0.0)
    arr = targets(47.0, 30.0, 60.0)
    assert lib.l3_band_keep_short(ctypes.byref(b), arr, 3) == 3
    assert [arr[i].rangeBin for i in range(3)] == [47.0, 30.0, 60.0]


def test_ball_track_armed_at_band_edge_acquires_a_departing_ball(lib):
    """The ball's first points beyond the band are inside the tracker's
    origin gate only when it is armed at the band's far edge."""
    b = band(lib, 47.0, 6.0)
    cfg = fw.BallTrackCfg()
    lib.l3_ball_track_cfg_defaults(ctypes.byref(cfg))
    ball = fw.BallTrack()
    lib.l3_ball_track_init(ctypes.byref(ball), ctypes.byref(cfg))
    origin = fw.Vec3()
    lib.l3_ball_track_arm(ctypes.byref(ball), b.hiBin, ctypes.byref(origin), 0)
    acquired = False
    # 2.5 bins per 3 ms frame (39 m/s): inside the core's 3-bin association gate.
    for frame, bin_ in enumerate((54.5, 57.0, 59.5, 62.0), start=1):
        arr = targets(bin_)
        arr[0].frame, arr[0].timestampUs, arr[0].confidence = frame, frame * 3000, 0.9
        arr[0].dopplerAliasMps = 5.0
        n = lib.l3_band_filter(ctypes.byref(b), arr, 1)
        acquired |= bool(
            lib.l3_ball_track_update_joint(
                ctypes.byref(ball), arr, n, frame, frame * 3000, fw.TRACK_NO_TARGET
            )
        )
    assert acquired
    assert ball.core.count >= 3


def depart(lib, b, arm_bin: float, bins) -> fw.BallTrack:
    """A ball departing through bins (one per 3 ms frame) with the band filter
    applied, the tracker armed at arm_bin; the tracker after the last frame."""
    cfg = fw.BallTrackCfg()
    lib.l3_ball_track_cfg_defaults(ctypes.byref(cfg))
    ball = fw.BallTrack()
    lib.l3_ball_track_init(ctypes.byref(ball), ctypes.byref(cfg))
    origin = fw.Vec3()
    lib.l3_ball_track_arm(ctypes.byref(ball), arm_bin, ctypes.byref(origin), 0)
    for frame, bin_ in enumerate(bins, start=1):
        arr = targets(bin_)
        arr[0].frame, arr[0].timestampUs, arr[0].confidence = frame, frame * 3000, 0.9
        arr[0].dopplerAliasMps = 5.0
        n = lib.l3_band_filter(ctypes.byref(b), arr, 1)
        lib.l3_ball_track_update_joint(
            ctypes.byref(ball), arr, n, frame, frame * 3000, fw.TRACK_NO_TARGET
        )
    return ball


# First seen 2.5 bins past the band's far edge (53), then 2.5 bins a frame:
# 8.5 bins past the ball at 47, beyond the tracker's 8-bin origin gate.
FIRST_SEEN_PAST_THE_BAND = (55.5, 58.0, 60.5, 63.0)


def test_the_same_ball_is_acquired_when_armed_at_the_band_edge(lib):
    b = band(lib, 47.0, 6.0)
    ball = depart(lib, b, b.hiBin, FIRST_SEEN_PAST_THE_BAND)
    assert ball.confirmed == 1
    assert ball.core.count == 4


def test_armed_at_the_ball_the_band_hides_every_point_its_origin_gate_would_take(lib):
    """Why the edge matters: armed at the ball (47) the origin gate reaches
    55, the band hides up to 53, and the ball first shows at 55.5 -- so the
    departing ball is never acquired."""
    b = band(lib, 47.0, 6.0)
    ball = depart(lib, b, 47.0, FIRST_SEEN_PAST_THE_BAND)
    assert ball.core.count == 0
    assert ball.confirmed == 0


def obs_row(values, stat="peak"):
    """l3_bin_obs_t per bin carrying `values` as the peak statistic."""
    arr = (fw.BinObs * len(values))()
    for i, v in enumerate(values):
        arr[i].peak = v
        arr[i].energy = v
    return arr


STAT = fw.STAT_NAMES["peak"]


def noisy_map(lib, first_bin, values, updates=8):
    noise = fw.BandNoise()
    lib.l3_band_noise_reset(ctypes.byref(noise))
    row = obs_row(values)
    for _ in range(updates):
        lib.l3_band_noise_update(ctypes.byref(noise), STAT, first_bin, row, len(values))
    return noise


def place(lib, noise, centre, width, search=10.0):
    out = fw.Band()
    lib.l3_band_place(ctypes.byref(noise), centre, search, width, ctypes.byref(out))
    return out


def test_noise_map_is_an_ema_of_the_statistic(lib):
    noise = fw.BandNoise()
    lib.l3_band_noise_reset(ctypes.byref(noise))
    lib.l3_band_noise_update(ctypes.byref(noise), STAT, 20, obs_row([16.0, 0.0]), 2)
    assert (noise.firstBin, noise.count, noise.updates) == (20, 2, 1)
    assert noise.avg[0] == pytest.approx(16.0)  # the first frame seeds the map
    lib.l3_band_noise_update(ctypes.byref(noise), STAT, 20, obs_row([0.0, 16.0]), 2)
    assert noise.avg[0] == pytest.approx(15.0) and noise.avg[1] == pytest.approx(1.0)


def test_noise_map_restarts_when_the_window_moves(lib):
    noise = noisy_map(lib, 20, [5.0] * 10)
    lib.l3_band_noise_update(ctypes.byref(noise), STAT, 32, obs_row([1.0] * 10), 10)
    assert (noise.firstBin, noise.updates) == (32, 1)
    assert noise.avg[0] == pytest.approx(1.0)


def test_placement_takes_the_noisiest_contiguous_run(lib):
    values = [1.0] * 53
    for b in range(24, 29):  # global bins 44..48 (first bin 20)
        values[b] = 50.0
    band = place(lib, noisy_map(lib, 20, values), centre=47.0, width=5.0)
    assert (band.valid, band.loBin, band.hiBin) == (1, 44.0, 48.0)


def test_placement_stays_in_the_search_window(lib):
    values = [1.0] * 53
    values[50] = 1000.0  # global 70: outside 47 +/- 10
    band = place(lib, noisy_map(lib, 20, values), centre=47.0, width=5.0)
    assert 37.0 <= band.loBin and band.hiBin <= 57.0


def test_ties_go_to_the_run_nearest_the_centre(lib):
    band = place(lib, noisy_map(lib, 20, [3.0] * 53), centre=47.0, width=5.0)
    assert (band.loBin, band.hiBin) == (45.0, 49.0)


def test_without_history_the_band_is_centred(lib):
    band = place(lib, noisy_map(lib, 20, [3.0] * 53, updates=7), centre=47.0, width=5.0)
    assert (band.loBin, band.hiBin) == (45.0, 49.0)


def test_width_wider_than_the_window_falls_back_to_centred(lib):
    band = place(lib, noisy_map(lib, 40, [3.0] * 6), centre=42.0, width=12.0, search=3.0)
    assert band.valid == 1
    assert band.hiBin - band.loBin == 11.0


def test_even_width_centred_is_deterministic(lib):
    # centred: lo = round(centre) - (width - 1) // 2 = 47 - 1
    band = place(lib, noisy_map(lib, 20, [3.0] * 53, updates=0), centre=47.0, width=4.0)
    assert (band.loBin, band.hiBin) == (46.0, 49.0)


def test_zero_width_is_no_band(lib):
    assert place(lib, noisy_map(lib, 20, [3.0] * 53), centre=47.0, width=0.0).valid == 0
