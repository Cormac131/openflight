"""Scoring the firmware's tracks against hand labels."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openflight.iwr6843 import label_scoring as ls, labels as lb


def _fw(frame, range_bin, doppler=0.0):
    return SimpleNamespace(frame=frame, range_bin=range_bin, doppler_mps=doppler)


def _lab(frame, range_bin, doppler=None):
    return lb.LabelPoint(frame, range_bin, doppler)


TOL = lb.Tolerances(range_bins=1.0, min_coverage=0.8)


def test_perfect_match_scores_one():
    s = ls.score_object([_lab(1, 10.0), _lab(2, 12.0)], [_fw(1, 10.0), _fw(2, 12.0)], TOL)
    assert (s.matched, s.false_points, s.coverage, s.score) == (2, 0, 1.0, 1.0)
    assert s.mean_abs_error_bins == 0.0


def test_within_tolerance_matches_and_costs_error():
    s = ls.score_object([_lab(1, 10.0)], [_fw(1, 10.5)], TOL)
    assert s.matched == 1
    assert s.mean_abs_error_bins == pytest.approx(0.5)
    assert s.score == pytest.approx(1.0 - ls.ERROR_WEIGHT * 0.5)


def test_outside_tolerance_is_a_miss_and_a_false_point():
    s = ls.score_object([_lab(1, 10.0)], [_fw(1, 12.5)], TOL)
    assert (s.matched, s.false_points, s.coverage) == (0, 1, 0.0)
    assert s.mean_abs_error_bins is None
    assert s.score == pytest.approx(0.0 - ls.FALSE_POINT_WEIGHT * 1)


def test_missing_frames_lower_coverage():
    s = ls.score_object([_lab(1, 10.0), _lab(2, 11.0)], [_fw(1, 10.0)], TOL)
    assert s.coverage == 0.5
    assert s.false_points == 0


def test_a_point_on_an_unlabelled_frame_is_false():
    s = ls.score_object([_lab(1, 10.0)], [_fw(1, 10.0), _fw(7, 40.0)], TOL)
    assert (s.matched, s.false_points) == (1, 1)


def test_only_the_closest_point_on_a_frame_can_match():
    s = ls.score_object([_lab(1, 10.0)], [_fw(1, 10.8), _fw(1, 10.1)], TOL)
    assert (s.matched, s.false_points) == (1, 1)
    assert s.mean_abs_error_bins == pytest.approx(0.1)


def test_reviewed_object_with_no_labels_is_perfect_when_nothing_is_tracked():
    s = ls.score_object([], [], TOL)
    assert (s.labelled, s.coverage, s.score) == (0, 1.0, 1.0)


def test_reviewed_object_with_no_labels_is_penalised_for_any_point():
    s = ls.score_object([], [_fw(3, 20.0)], TOL)
    assert s.false_points == 1
    assert s.score == pytest.approx(1.0 - ls.FALSE_POINT_WEIGHT)


def test_doppler_tolerance_applies_only_when_label_has_a_doppler():
    tol = lb.Tolerances(range_bins=1.0, min_coverage=0.8, doppler_mps=2.0)
    assert ls.score_object([_lab(1, 10.0, 30.0)], [_fw(1, 10.0, 34.0)], tol).matched == 0
    assert ls.score_object([_lab(1, 10.0, 30.0)], [_fw(1, 10.0, 31.0)], tol).matched == 1
    assert ls.score_object([_lab(1, 10.0)], [_fw(1, 10.0, 99.0)], tol).matched == 1


def _labels(ball=(), club=(), tol=TOL):
    return lb.Labels(
        dump="d.l3dump", dump_sha256="x", reviewed=True, ball=ball, club=club, tolerances=tol
    )


def test_score_labels_reads_club_and_ball_points_from_the_result():
    result = SimpleNamespace(points=[_fw(1, 30.0)], ball_points=[_fw(5, 50.0)])
    scores = ls.score_labels(_labels(ball=(_lab(5, 50.0),), club=(_lab(1, 30.0),)), result)
    assert set(scores) == {"ball", "club"}
    assert scores["ball"].matched == 1 and scores["club"].matched == 1
    assert ls.dump_score(scores) == 1.0


def test_check_labels_fails_on_low_coverage():
    result = SimpleNamespace(points=[], ball_points=[_fw(5, 50.0)])
    labels = _labels(ball=(_lab(5, 50.0),), club=(_lab(1, 30.0),))
    failures = ls.check_labels(labels, ls.score_labels(labels, result))
    assert failures == ["club: coverage 0.00 below 0.80"]


def test_check_labels_fails_on_a_point_for_an_object_labelled_empty():
    result = SimpleNamespace(points=[], ball_points=[_fw(5, 50.0)])
    labels = _labels(ball=(), club=())
    failures = ls.check_labels(labels, ls.score_labels(labels, result))
    assert failures == ["ball: 1 firmware point(s) but the object is labelled as not tracked"]


def test_check_labels_passes_when_everything_matches():
    result = SimpleNamespace(points=[_fw(1, 30.0)], ball_points=[])
    labels = _labels(club=(_lab(1, 30.0),))
    assert ls.check_labels(labels, ls.score_labels(labels, result)) == []


def test_baseline_round_trip_and_missing_file(tmp_path):
    assert ls.load_baseline(tmp_path) == {}
    ls.write_baseline(tmp_path, {"b.l3dump": 0.5, "a.l3dump": 0.9})
    assert ls.load_baseline(tmp_path) == {"a.l3dump": 0.9, "b.l3dump": 0.5}
    text = (tmp_path / ls.BASELINE_NAME).read_text(encoding="utf-8")
    assert list(json.loads(text)) == ["a.l3dump", "b.l3dump"]
