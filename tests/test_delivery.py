"""Tests for openflight.delivery: inferred face, smash and plausibility gates."""

from __future__ import annotations

import pytest

from openflight.clubs import ClubType
from openflight.delivery import (
    FACE_WEIGHT_DRIVER,
    FACE_WEIGHT_IRON,
    build_report,
    check_plausibility,
    face_weight,
    infer_face,
    smash_factor,
)


def test_face_weight_is_higher_for_the_driver_and_woods_than_irons():
    assert face_weight(ClubType.DRIVER) == FACE_WEIGHT_DRIVER
    assert face_weight(ClubType.WOOD_3) == FACE_WEIGHT_DRIVER
    assert face_weight(ClubType.IRON_7) == FACE_WEIGHT_IRON
    assert face_weight(None) == FACE_WEIGHT_IRON


def test_face_is_solved_from_start_and_path_and_labelled_inferred():
    # A square face on an in-to-out path starts the ball part way toward the path.
    face = infer_face(club_path_deg=4.0, ball_start_deg=0.25 * 4.0, club=ClubType.IRON_7)
    assert face.face_deg == pytest.approx(0.0, abs=1e-9)
    assert face.face_to_path_deg == pytest.approx(-4.0)
    assert face.source == "inferred" and face.label == "ESTIMATED"
    # An open face on a square path starts the ball right; face-to-path is open.
    fade = infer_face(0.0, 3.0, ClubType.DRIVER)
    assert fade.face_deg == pytest.approx(3.0 / FACE_WEIGHT_DRIVER)
    assert fade.face_to_path_deg > 0
    # Left of the target line is negative throughout, as in l3_frames.h.
    hook = infer_face(2.0, -4.0, ClubType.IRON_7)
    assert hook.face_deg < 0 and hook.face_to_path_deg < 0


def test_smash_factor_needs_a_club_speed():
    assert smash_factor(60.0, 40.0) == pytest.approx(1.5)
    assert smash_factor(60.0, 0.0) is None
    assert smash_factor(60.0, None) is None


def test_plausibility_passes_a_normal_shot_and_names_every_failure():
    ok = check_plausibility(
        ball_speed_mps=65.0,
        club_speed_mps=45.0,
        vertical_launch_deg=12.0,
        horizontal_launch_deg=1.5,
        club_path_deg=2.0,
        angle_of_attack_deg=-3.0,
        face_deg=1.0,
    )
    assert ok.ok and ok.reasons == ()
    bad = check_plausibility(
        ball_speed_mps=130.0,
        club_speed_mps=3.0,
        vertical_launch_deg=70.0,
        horizontal_launch_deg=50.0,
        club_path_deg=35.0,
        angle_of_attack_deg=25.0,
        face_deg=50.0,
    )
    assert not bad.ok
    assert len(bad.reasons) == 8
    assert any("smash" in r and "doubt both" in r for r in bad.reasons)
    # Missing values are not failures.
    assert check_plausibility(ball_speed_mps=60.0).ok


def test_impossible_smash_alone_fails_the_gate():
    result = check_plausibility(ball_speed_mps=60.0, club_speed_mps=30.0)
    assert not result.ok and result.reasons[0].startswith("smash 2.00")


def test_report_keeps_measured_and_estimated_apart():
    report = build_report(
        club_speed_mps=44.7,
        club_path_deg=2.0,
        angle_of_attack_deg=-1.4,
        ball_speed_mps=67.0,
        horizontal_launch_deg=1.0,
        club=ClubType.DRIVER,
    )
    assert report.club_speed_mph == pytest.approx(100.0, abs=0.1)
    assert report.smash == pytest.approx(1.499, abs=1e-3)
    assert report.face is not None and report.plausibility.ok
    lines = report.lines()
    assert lines[0] == "MEASURED" and "ESTIMATED" in lines
    measured = lines[1 : lines.index("ESTIMATED")]
    assert any("club speed" in line and "+100.0 mph" in line for line in measured)
    estimated = lines[lines.index("ESTIMATED") + 1 :]
    assert any(line.strip().startswith("face angle") for line in estimated)
    assert any(line.strip().startswith("smash factor") and "1.50" in line for line in estimated)
    assert not any("IMPLAUSIBLE" in line for line in lines)


def test_report_without_a_club_has_no_face_or_smash_and_flags_implausible_values():
    report = build_report(
        club_speed_mps=None,
        club_path_deg=None,
        angle_of_attack_deg=None,
        ball_speed_mps=140.0,
        horizontal_launch_deg=2.0,
    )
    assert report.face is None and report.smash is None
    assert not report.plausibility.ok
    assert report.lines()[-1].startswith("IMPLAUSIBLE: ball speed 140.0")
    assert report.club_speed_mph is None
