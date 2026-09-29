"""Impact evaluation: method C and the A-vs-C metrics (impact_eval.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from iwr6843_synth import synth_shot_dump

from openflight.iwr6843 import firmware_host as fw, firmware_replay as fr, impact_eval as ie

BALL_M = 2.20
IMPACT_US = 30_000.0


def line(speed, times, offset_us=0.0):
    return [(t, BALL_M + speed * (t - IMPACT_US - offset_us) * 1e-6) for t in times]


CLUB_IN = line(30.0, (9_000, 12_000, 15_000, 18_000))
CLUB_OUT = line(25.0, (42_000, 45_000, 48_000, 51_000))
BALL_OUT = line(60.0, (36_000, 39_000, 42_000, 45_000))


def test_joint_fit_recovers_impact_on_clean_tracks():
    assert ie.joint_fit_impact(CLUB_IN, CLUB_OUT, BALL_OUT, BALL_M) == pytest.approx(
        IMPACT_US, abs=50
    )


def test_joint_fit_needs_two_tracks_of_three_points():
    assert ie.joint_fit_impact(CLUB_IN, [], [], BALL_M) is None
    assert ie.joint_fit_impact(CLUB_IN, CLUB_OUT[:2], [], BALL_M) is None
    assert ie.joint_fit_impact(CLUB_IN, [], BALL_OUT, BALL_M) == pytest.approx(IMPACT_US, abs=50)


def test_joint_fit_uses_the_points_nearest_the_band():
    """club_in's last four, club_out's and ball_out's first four: points far
    from impact (here on a different line) are ignored, as method A does."""
    stray_early = line(30.0, (0, 3_000), offset_us=8_000)
    stray_late = line(60.0, (60_000, 63_000), offset_us=8_000)
    fit = ie.joint_fit_impact(stray_early + CLUB_IN, [], BALL_OUT + stray_late, BALL_M)
    assert fit == pytest.approx(IMPACT_US, abs=50)


def test_leave_one_out_spread_is_small_when_tracks_agree_and_large_when_not():
    agree = ie.leave_one_out_spread_us(CLUB_IN, CLUB_OUT, BALL_OUT, BALL_M)
    assert agree is not None and agree <= 100
    skewed = line(60.0, (36_000, 39_000, 42_000, 45_000), offset_us=6_000)
    assert ie.leave_one_out_spread_us(CLUB_IN, CLUB_OUT, skewed, BALL_M) > 2_000


def test_leave_one_out_spread_needs_all_three_tracks():
    assert ie.leave_one_out_spread_us(CLUB_IN, CLUB_OUT, [], BALL_M) is None
    assert ie.leave_one_out_spread_us(CLUB_IN, CLUB_OUT, BALL_OUT[:2], BALL_M) is None


def outcome(verdict, spread, c_spread, dtrig=-3_000.0, in_band=0, tracks=("club_in", "ball_out")):
    return ie.ImpactOutcome("x", verdict, tracks, spread, dtrig, c_spread, in_band)


def test_summary_counts_verdicts_and_medians():
    s = ie.summarize_impact(
        [
            outcome("consistent", 200.0, 150.0),
            outcome("consistent", 400.0, 250.0),
            outcome("inconsistent", 5_000.0, None),
            outcome("none", None, None, dtrig=None, tracks=()),
        ]
    )
    assert s["captures"] == 4 and s["with_estimate"] == 3
    assert (s["consistent"], s["inconsistent"], s["none"]) == (2, 1, 1)
    assert s["median_spread_us"] == pytest.approx(300.0)  # consistent captures only
    assert s["median_c_spread_us"] == pytest.approx(200.0)
    assert s["median_refined_minus_trigger_us"] == pytest.approx(-3_000.0)
    assert s["club_points_in_band"] == 0


def test_summary_counts_single_track_estimates():
    """The firmware's fourth verdict: an estimate from one track, no agreement
    check. It counts toward with_estimate, so every estimate is in one bucket."""
    s = ie.summarize_impact(
        [
            outcome("single_track", 0.0, None),
            outcome("consistent", 200.0, 150.0),
            outcome("none", None, None, dtrig=None, tracks=()),
        ]
    )
    assert s["with_estimate"] == 2 and s["single_track"] == 1
    assert s["consistent"] + s["inconsistent"] + s["single_track"] == s["with_estimate"]


def test_summary_sums_club_points_in_band():
    s = ie.summarize_impact(
        [outcome("consistent", 1.0, 1.0, in_band=2), outcome("none", None, None, in_band=3)]
    )
    assert s["club_points_in_band"] == 5


def test_summary_of_nothing_has_no_medians_and_c_does_not_win():
    s = ie.summarize_impact([])
    assert s["captures"] == 0
    assert s["median_spread_us"] is None and s["median_c_spread_us"] is None
    assert s["c_wins"] is False


def test_c_wins_only_with_a_thirty_percent_cut():
    better = ie.summarize_impact([outcome("consistent", 1_000.0, 690.0)])
    worse = ie.summarize_impact([outcome("consistent", 1_000.0, 710.0)])
    assert better["c_wins"] is True and worse["c_wins"] is False


def test_c_does_not_win_when_it_is_available_on_fewer_captures():
    s = ie.summarize_impact(
        [outcome("consistent", 1_000.0, 100.0), outcome("consistent", 1_000.0, None)]
    )
    assert s["median_c_spread_us"] == pytest.approx(100.0)
    assert s["c_wins"] is False


# --- impact_outcome on a replay result -------------------------------------

BIN_M = 6.0 / 128
DEST_BIN = 47  # 2.203 m


def pt(frame, t_us, range_m):
    return SimpleNamespace(
        frame=frame, timestamp_us=t_us, range_m=range_m, range_bin=range_m / BIN_M
    )


def fit_summary(verdict="consistent", spread=120.0, dtrig=-2_500.0, whys=("ok", "ok", "ok")):
    names = ("club_in", "club_out", "ball_out")
    tracks = {
        n: fr.TrackEstimateSummary(w, 4, 30_000.0, 100.0, 30.0)
        for n, w in zip(names, whys, strict=True)
    }
    return fr.ImpactFitSummary(verdict, 30_000.0, spread, dtrig, None, False, tracks)


def fake_result(fit, *, band=None, impact_frame=5, points=None, ball_points=None):
    ball = DEST_BIN * BIN_M
    if points is None:
        points = [
            pt(i, t, r)
            for i, (t, r) in enumerate(line(30.0, (9_000, 12_000, 15_000, 18_000)), start=2)
        ]
        points += [
            pt(i, t, r)
            for i, (t, r) in enumerate(line(25.0, (42_000, 45_000, 48_000, 51_000)), start=9)
        ]
    if ball_points is None:
        ball_points = [
            pt(i, t, r)
            for i, (t, r) in enumerate(line(60.0, (36_000, 39_000, 42_000, 45_000)), start=7)
        ]
    assert abs(ball - BALL_M) < BIN_M  # the synthetic lines sit on the destination
    return SimpleNamespace(
        impact_fit=fit,
        config=fr.ReplayConfig(tee_bin=DEST_BIN),
        shot=SimpleNamespace(impactFrame=impact_frame),
        band=band,
        points=points,
        ball_points=ball_points,
    )


def test_impact_outcome_reads_method_a_and_runs_method_c():
    o = ie.impact_outcome("cap.l3dump", fake_result(fit_summary(), impact_frame=6))
    assert o.name == "cap.l3dump" and o.verdict == "consistent"
    assert o.tracks_ok == ("club_in", "club_out", "ball_out")
    assert o.spread_us == pytest.approx(120.0)
    assert o.refined_minus_trigger_us == pytest.approx(-2_500.0)
    # The destination bin is 3 mm off BALL_M: the three tracks still agree closely.
    assert o.c_spread_us is not None and o.c_spread_us < 500
    assert o.club_points_in_band == 0


def test_impact_outcome_counts_only_ok_tracks():
    o = ie.impact_outcome("x", fake_result(fit_summary(whys=("ok", "uncertain", "few"))))
    assert o.tracks_ok == ("club_in",)


def test_impact_outcome_without_a_fit_is_none():
    o = ie.impact_outcome("x", fake_result(None))
    assert (o.verdict, o.tracks_ok, o.spread_us, o.refined_minus_trigger_us, o.c_spread_us) == (
        "none",
        (),
        None,
        None,
        None,
    )


def test_a_none_verdict_has_no_spread():
    o = ie.impact_outcome("x", fake_result(fit_summary(verdict="none", spread=0.0, dtrig=None)))
    assert o.verdict == "none" and o.spread_us is None


def test_club_points_in_band_is_zero_with_the_band_off():
    inside = [pt(i, 20_000 + i, DEST_BIN * BIN_M) for i in range(3)]
    o = ie.impact_outcome("x", fake_result(fit_summary(), band=None, points=inside, impact_frame=5))
    assert o.club_points_in_band == 0


def test_club_points_in_band_counts_club_points_up_to_impact_inside_the_band():
    inside_before = [pt(i, 20_000 + i, DEST_BIN * BIN_M) for i in range(3)]
    inside_after = [pt(9, 40_000, DEST_BIN * BIN_M)]  # club_out: not the pre-impact club
    outside = [pt(4, 21_000, (DEST_BIN - 10) * BIN_M)]
    result = fake_result(
        fit_summary(),
        band=(DEST_BIN - 6.0, DEST_BIN + 6.0),
        points=inside_before + outside + inside_after,
        impact_frame=5,
    )
    assert ie.impact_outcome("x", result).club_points_in_band == 3
    # Without an impact every club point is pre-impact.
    no_fit = fake_result(
        None, band=(DEST_BIN - 6.0, DEST_BIN + 6.0), points=inside_before + inside_after
    )
    assert ie.impact_outcome("x", no_fit).club_points_in_band == 4


@pytest.mark.skipif(fw.host_compiler() is None, reason="no C compiler for the firmware modules")
def test_impact_outcome_on_a_replayed_synthetic_shot():
    dump = synth_shot_dump(ball_speed_ms=60.0, vla_deg=12.0, hla_deg=0.0, tee_range_m=1.372)
    result = fr.replay_dump(dump, fr.ReplayConfig(tee_bin=29))
    o = ie.impact_outcome("synth", result)
    assert o.verdict in ("consistent", "inconsistent", "none")
    assert o.club_points_in_band == 0  # band off by default
    banded = fr.replay_dump(dump, fr.ReplayConfig(tee_bin=29, band_bins=6.0))
    assert banded.band is not None
    assert ie.impact_outcome("synth", banded).club_points_in_band == 0
