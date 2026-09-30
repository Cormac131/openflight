"""Tests for the IWR6843 scan plan, firmware/iwr6843/l3_scan.c.

The board scores a range bin in ~73 us (triggerLog perf, 2026-09-30) and has
3 ms a frame. With the tee band on it scored the trigger region and then the
whole window (~5.1 ms), and the detect task, which outranks the CLI and the
trigger notices, starved them as soon as the trigger was armed. The scan plan
says which global bins a frame scores:

- before impact: the club's approach short of the band (clubBins), the
  ball-leave fallback's stretch beyond it (leaveBins), the trigger region
  clipped to short of the band, and on idle frames a rotating chunk of the
  band's interior for its noise map
- after impact: postBins that follow the ball track (postBehindBins short of
  its predicted bin), from just beyond the band until the ball is tracked
"""

from __future__ import annotations

import ctypes

import pytest

from openflight.iwr6843 import firmware_host as fw

WINDOW = (20, 53)  # global first bin, count: the kiosk's 53-bin profile


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def cfg(lib, **overrides) -> fw.ScanCfg:
    out = fw.ScanCfg()
    lib.l3_scan_cfg_defaults(ctypes.byref(out))
    for name, value in overrides.items():
        setattr(out, name, value)
    return out


def band(lo: float, hi: float, valid: bool = True) -> fw.Band:
    out = fw.Band()
    out.valid, out.loBin, out.hiBin = int(valid), lo, hi
    return out


def span(s: fw.Span) -> tuple[int, int]:
    return (s.first, s.count)


def pre(lib, b: fw.Band, region=(28, 16), window=WINDOW, **overrides):
    region_out, club, leave = fw.Span(), fw.Span(), fw.Span()
    lib.l3_scan_pre(
        ctypes.byref(cfg(lib, **overrides)),
        window[0],
        window[1],
        region[0],
        region[1],
        ctypes.byref(b),
        ctypes.byref(region_out),
        ctypes.byref(club),
        ctypes.byref(leave),
    )
    return span(region_out), span(club), span(leave)


def count(lib, *spans: tuple[int, int]) -> int:
    arr = (fw.Span * len(spans))(*(fw.Span(first, n) for first, n in spans))
    return lib.l3_scan_count(arr, len(spans))


def test_defaults(lib):
    c = cfg(lib)
    assert (c.clubBins, c.leaveBins, c.mapChunkBins) == (16, 10, 2)


def test_before_impact_the_club_reads_short_of_the_band_and_the_fallback_beyond_it(lib):
    """Band 40.0-45.0: the club takes 24..40 (the edge bin as the last peak's
    neighbour), the fallback 46..55, the trigger region is clipped to 28..40."""
    region, club, leave = pre(lib, band(40.0, 45.0))
    assert club == (24, 17)
    assert leave == (46, 10)
    assert region == (28, 13)


def test_sub_bin_band_edges_round_outward_to_whole_bins(lib):
    _region, club, leave = pre(lib, band(40.6, 45.4))
    assert club == (24, 17)
    assert leave == (46, 10)


def test_an_armed_swing_frame_scores_27_bins(lib):
    region, club, leave = pre(lib, band(40.0, 45.0))
    assert count(lib, region, club, leave) == 27


def test_spans_are_clipped_to_the_window(lib):
    _region, club, leave = pre(lib, band(24.0, 29.0), window=(20, 32))
    assert club == (20, 5)
    assert leave == (30, 10)
    _region, club, leave = pre(lib, band(44.0, 49.0), window=(20, 32))
    assert club == (28, 17)
    assert leave == (50, 2), "short of leaveBins: the fallback then has too few bins to judge"


def test_without_a_band_the_club_reads_the_trigger_region_and_nothing_beyond(lib):
    region, club, leave = pre(lib, band(0.0, 0.0, valid=False))
    assert region == (28, 16) and club == (28, 16)
    assert leave == (0, 0)


def test_a_band_short_of_the_region_leaves_no_region(lib):
    region, _club, _leave = pre(lib, band(20.0, 25.0), region=(28, 16))
    assert region == (28, 0)


def test_count_is_the_distinct_bins_the_union_covers(lib):
    assert count(lib, (10, 5), (12, 5)) == 7
    assert count(lib, (10, 5), (20, 5)) == 10
    assert count(lib, (10, 0), (20, 5)) == 5
    assert count(lib, (10, 5)) == 5


# --- the band's noise map on idle frames ----------------------------------------


def chunk(lib, b: fw.Band, cursor: ctypes.c_uint32, window=WINDOW, **overrides):
    s = fw.Span()
    lib.l3_scan_map_chunk(
        ctypes.byref(cfg(lib, **overrides)),
        window[0],
        window[1],
        ctypes.byref(b),
        ctypes.byref(cursor),
        ctypes.byref(s),
    )
    return span(s)


def test_the_map_chunk_walks_the_band_interior_and_wraps(lib):
    """Band 40.0-45.0: its interior 41..45 (5 bins) is what nothing else scores."""
    cursor = ctypes.c_uint32(0)
    seen = [chunk(lib, band(40.0, 45.0), cursor) for _ in range(4)]
    assert seen == [(41, 2), (43, 2), (45, 1), (41, 2)]


def test_an_idle_frame_scores_29_bins(lib):
    b = band(40.0, 45.0)
    region, club, leave = pre(lib, b)
    cursor = ctypes.c_uint32(0)
    assert count(lib, region, club, leave, chunk(lib, b, cursor)) == 29


def test_no_band_no_map_chunk(lib):
    assert chunk(lib, band(0.0, 0.0, valid=False), ctypes.c_uint32(0)) == (0, 0)


# --- after impact -----------------------------------------------------------------
#
# 16 bins, split: postBins following the ball (postBehindBins short of its
# predicted bin), postClubBins following the club (a bin short of its
# prediction; just beyond the band until the club track takes the
# follow-through). The ball alone lost the club after impact: usable club_out
# 6 -> 0 and consistent impact fits 7 -> 2 on the labelled swings; 12 + 4 kept
# 5 and 7 with the same 25 launches.


def post(lib, b: fw.Band, ball=None, club=None, window=WINDOW, **overrides):
    """ball / club: the predicted bin when tracked, None when not."""
    ball_span, club_span = fw.Span(), fw.Span()
    lib.l3_scan_post(
        ctypes.byref(cfg(lib, **overrides)),
        window[0],
        window[1],
        ctypes.byref(b),
        0 if ball is None else 1,
        0.0 if ball is None else ball,
        0 if club is None else 1,
        0.0 if club is None else club,
        ctypes.byref(ball_span),
        ctypes.byref(club_span),
    )
    return span(ball_span), span(club_span)


def test_post_defaults(lib):
    c = cfg(lib)
    assert (c.postBins, c.postBehindBins, c.postClubBins) == (12, 3, 4)


def test_before_either_is_tracked_both_start_just_beyond_the_band(lib):
    """The band's own bins are dropped (l3_band_filter) and the ball tracker
    starts only beyond its far edge: a window from its near edge wasted them,
    and on an 11-bin band a 60 m/s ball was past it before it was seen."""
    assert post(lib, band(40.0, 45.0)) == ((46, 12), (46, 4))


def test_a_tracked_ball_is_followed_three_bins_behind_its_prediction(lib):
    ball, _club = post(lib, band(40.0, 45.0), ball=55.7)
    assert ball == (52, 12)


def test_the_ball_window_never_falls_back_into_the_band(lib):
    ball, _club = post(lib, band(40.0, 45.0), ball=47.0)
    assert ball == (46, 12)


def test_a_tracked_club_is_followed_a_bin_behind_its_prediction(lib):
    _ball, club = post(lib, band(40.0, 45.0), club=47.6)
    assert club == (46, 4)


def test_post_windows_stay_whole_at_the_window_end(lib):
    ball, club = post(lib, band(40.0, 45.0), ball=71.0, club=72.5)
    assert ball == (61, 12) and club == (69, 4)


def test_the_post_windows_score_16_bins_apart_and_fewer_together(lib):
    ball, club = post(lib, band(40.0, 45.0), ball=60.0)
    assert count(lib, ball, club) == 16
    ball, club = post(lib, band(40.0, 45.0))
    assert count(lib, ball, club) == 12, "46..57 holds 46..49"


def merge(lib, a, b):
    out = (fw.Span * 2)()
    n = lib.l3_scan_merge(fw.Span(*a), fw.Span(*b), out)
    return [span(out[i]) for i in range(n)]


def test_overlapping_or_touching_spans_merge_so_nothing_is_extracted_twice(lib):
    assert merge(lib, (40, 12), (46, 4)) == [(40, 12)]
    assert merge(lib, (40, 12), (52, 4)) == [(40, 16)]
    assert merge(lib, (52, 12), (46, 4)) == [(46, 4), (52, 12)], "50..51 between them"
    assert merge(lib, (50, 12), (46, 4)) == [(46, 16)]
    assert merge(lib, (52, 12), (40, 4)) == [(40, 4), (52, 12)]
    assert merge(lib, (40, 0), (46, 4)) == [(46, 4)]
