"""Tests for src/openflight/iwr6843/zone_report.py: sorting a replay's club
track points into impact, club and stray against the labels, and tallying
what the swing zone kept."""

from __future__ import annotations

import pytest

from openflight.iwr6843 import firmware_host as fw, firmware_replay as fr
from openflight.iwr6843.board_calibration import BoardCalibration
from openflight.iwr6843.labels import LabelPoint, Labels
from openflight.iwr6843.swing_zone import SwingZone, ZoneVerdict
from openflight.iwr6843.zone_report import (
    IMPACT_FRAMES,
    Tally,
    format_totals,
    point_kind,
    run_report,
    tally_recording,
    totals,
)


def labels(club=(), ball=()) -> Labels:
    return Labels(
        dump="x.l3dump",
        dump_sha256="0" * 64,
        reviewed=True,
        club=tuple(LabelPoint(frame=f, range_bin=b) for f, b in club),
        ball=tuple(LabelPoint(frame=f, range_bin=b) for f, b in ball),
    )


def point(frame: int, range_bin: float) -> fr.PointSummary:
    return fr.PointSummary(
        frame=frame,
        timestamp_us=frame * 3000,
        range_bin=range_bin,
        range_m=0.0,
        doppler_mps=0.0,
        confidence=1.0,
    )


INSIDE = ZoneVerdict(())
LEFT = ZoneVerdict(("left",))


def test_points_on_the_club_near_launch_are_impact():
    lab = labels(club=[(5, 30.0), (9, 38.0), (10, 39.0)], ball=[(10, 41.0)])
    assert point_kind(point(10, 39.4), lab) == "impact"
    assert point_kind(point(10 - IMPACT_FRAMES, 30.0), lab) == "stray", "no club label there"
    assert point_kind(point(9, 38.0 + 1.5), lab) == "impact"
    assert point_kind(point(5, 30.0), lab) == "club", "before the impact window"
    assert point_kind(point(9, 36.0), lab) == "stray", "too far from the label"
    assert point_kind(point(11, 39.0), lab) == "stray", "no club label on that frame"


def test_without_a_ball_a_club_point_is_never_impact():
    assert point_kind(point(5, 30.0), labels(club=[(5, 30.0)])) == "club"


def test_a_recording_is_tallied_up_to_its_fire():
    lab = labels(club=[(8, 35.0), (10, 39.0)], ball=[(10, 41.0)])
    points = [point(8, 35.0), point(10, 39.0), point(10, 45.0), point(12, 39.0)]
    verdicts = [INSIDE, INSIDE, LEFT, INSIDE]
    tallies = tally_recording(points, verdicts, lab, fired_frame=10)
    assert (tallies["impact"].points, tallies["impact"].inside) == (2, 2)
    assert (tallies["stray"].points, tallies["stray"].inside) == (1, 0)
    assert tallies["stray"].reasons == {"left": 1}
    unfired = tally_recording(points, verdicts, lab, fired_frame=None)
    assert sum(t.points for t in unfired.values()) == 4


def test_points_and_verdicts_must_line_up():
    with pytest.raises(ValueError):
        tally_recording([point(1, 1.0)], [], labels(), None)


def test_totals_add_up_and_print_every_kind():
    from openflight.iwr6843.zone_report import RecordingResult

    a = RecordingResult(
        "a", {"impact": Tally(2, 1, {"low": 1}), "club": Tally(), "stray": Tally(3, 0, {"left": 3})}
    )
    b = RecordingResult(
        "b", {"impact": Tally(2, 2), "club": Tally(1, 1), "stray": Tally(1, 0, {"left": 1})}
    )
    total = totals([a, b])
    assert (total["impact"].points, total["impact"].inside) == (4, 3)
    assert total["stray"].reasons == {"left": 4} and total["stray"].share_inside == 0.0
    assert Tally().share_inside is None
    text = format_totals([a, b])
    assert "2 recordings" in text and "impact" in text and "left 4" in text


@pytest.mark.skipif(fw.host_compiler() is None, reason="no C compiler for the firmware modules")
def test_the_report_runs_over_a_recordings_folder():
    folder = fr.RECORDINGS_DIR / "golfer_2026-09"
    if not folder.exists():
        pytest.skip("no golfer recordings")
    results = run_report(
        [folder],
        SwingZone(),
        BoardCalibration.from_file("config/iwr6843_calibration_reference.json"),
    )
    assert results and all(set(r.tallies) == {"impact", "club", "stray"} for r in results)
    total = totals(results)
    assert total["impact"].points > 0 and total["stray"].points > 0
    wide = totals(
        run_report(
            [folder],
            SwingZone(
                short_m=10.0,
                past_m=10.0,
                half_width_m=10.0,
                min_height_m=-10.0,
                max_height_m=10.0,
                require_angles=False,
            ),
            BoardCalibration.from_file("config/iwr6843_calibration_reference.json"),
        )
    )
    assert all(t.inside == t.points for t in wide.values()), (
        "a corridor around everything keeps everything"
    )
