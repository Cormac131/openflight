"""Scoring the firmware's tracks against hand labels."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from iwr6843_synth import synth_shot_dump

from openflight.iwr6843 import (
    firmware_host as fw,
    firmware_replay as fr,
    label_scoring as ls,
    labels as lb,
)

needs_compiler = pytest.mark.skipif(
    fw.host_compiler() is None, reason="no C compiler for the firmware modules"
)
TEE_BIN = int(1.372 / (6.0 / 128))


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


def test_a_freshly_written_baseline_passes_even_for_a_score_that_rounds_up(tmp_path):
    two_thirds = 2 / 3
    ls.write_baseline(tmp_path, {"d.l3dump": two_thirds})
    assert ls.load_baseline(tmp_path)["d.l3dump"] == two_thirds
    labels = _labels(club=(_lab(1, 30.0), _lab(2, 30.0), _lab(3, 30.0)), tol=TOL)
    scores = {
        "club": ls.ObjectScore(3, 3, 3, 0, 0.0, 1.0, two_thirds),
        "ball": ls.ObjectScore(0, 0, 0, 0, None, 1.0, two_thirds),
    }
    assert ls.dump_score(scores) == two_thirds
    assert ls.check_against_baseline("d.l3dump", labels, scores, ls.load_baseline(tmp_path)) == []


def test_check_against_baseline_passes_exactly_at_min_coverage(tmp_path):
    tol = lb.Tolerances(range_bins=1.0, min_coverage=0.5)
    labels = _labels(club=(_lab(1, 30.0), _lab(2, 30.0)), tol=tol)
    scores = ls.score_labels(labels, SimpleNamespace(points=[_fw(1, 30.0)], ball_points=[]))
    assert scores["club"].coverage == 0.5
    ls.write_baseline(tmp_path, {"d.l3dump": ls.dump_score(scores)})
    assert ls.check_against_baseline("d.l3dump", labels, scores, ls.load_baseline(tmp_path)) == []


def test_check_against_baseline_reports_a_missing_entry():
    labels = _labels(club=(_lab(1, 30.0),))
    scores = ls.score_labels(labels, SimpleNamespace(points=[_fw(1, 30.0)], ball_points=[]))
    failures = ls.check_against_baseline("d.l3dump", labels, scores, {})
    assert len(failures) == 1 and "no baseline score" in failures[0]
    assert "--update-baseline" in failures[0]


def test_check_against_baseline_reports_a_score_below_the_baseline():
    labels = _labels(club=(_lab(1, 30.0),))
    scores = ls.score_labels(labels, SimpleNamespace(points=[_fw(1, 30.0)], ball_points=[]))
    assert ls.dump_score(scores) == 1.0
    failures = ls.check_against_baseline("d.l3dump", labels, scores, {"d.l3dump": 1.01})
    assert len(failures) == 1 and "below the baseline" in failures[0]


def test_check_against_baseline_includes_the_coverage_failures():
    labels = _labels(club=(_lab(1, 30.0),))
    scores = ls.score_labels(labels, SimpleNamespace(points=[], ball_points=[]))
    failures = ls.check_against_baseline("d.l3dump", labels, scores, {"d.l3dump": -9.0})
    assert failures == ["club: coverage 0.00 below 0.80"]


# --- end to end on a synthetic recording ---------------------------------------


def _recordings_dir(tmp_path):
    """A recordings folder with one synthetic dump and a manifest entry for it."""
    (tmp_path / "synth.l3dump").write_bytes(synth_shot_dump(ball_speed_ms=60.0, tee_range_m=1.372))
    (tmp_path / "manifest.json").write_text(
        json.dumps({"default": {"tee_bin": TEE_BIN, "dest_bin": TEE_BIN}}), encoding="utf-8"
    )
    return tmp_path


def _labels_from_result(dump, result, *, shift=0.0, unseen_ball_frames=(), reviewed=True):
    base = lb.empty_labels(dump)
    ball = tuple(lb.LabelPoint(p.frame, p.range_bin + shift) for p in result.ball_points)
    # frames a reviewer labelled that the firmware never tracked: a miss
    ball += tuple(lb.LabelPoint(f, 20.0) for f in unseen_ball_frames)
    club = tuple(lb.LabelPoint(p.frame, p.range_bin) for p in result.points)
    return lb.Labels(base.dump, base.dump_sha256, reviewed, ball=ball, club=club)


@needs_compiler
def test_reviewed_recordings_pairs_each_labelled_dump_with_its_manifest_config(tmp_path):
    directory = _recordings_dir(tmp_path)
    dump = directory / "synth.l3dump"
    assert ls.reviewed_recordings(directory) == []  # no labels yet
    result = fr.replay_dump(dump.read_bytes(), fr.recording_configs(directory)[0][1])
    lb.save_labels(dump, _labels_from_result(dump, result, reviewed=False))
    assert ls.reviewed_recordings(directory) == []  # not reviewed
    lb.save_labels(dump, _labels_from_result(dump, result))
    (found,) = ls.reviewed_recordings(directory)
    assert found[0] == dump and found[1].dest_bin == TEE_BIN and found[2].reviewed


@needs_compiler
def test_a_dump_that_changed_after_labelling_fails_loudly(tmp_path):
    directory = _recordings_dir(tmp_path)
    dump = directory / "synth.l3dump"
    result = fr.replay_dump(dump.read_bytes(), fr.recording_configs(directory)[0][1])
    lb.save_labels(dump, _labels_from_result(dump, result))
    dump.write_bytes(dump.read_bytes() + b"\x00")
    with pytest.raises(lb.LabelError, match="changed since it was labelled"):
        ls.reviewed_recordings(directory)


@needs_compiler
def test_labels_equal_to_the_firmware_track_score_one_and_pass(tmp_path):
    directory = _recordings_dir(tmp_path)
    dump, config = fr.recording_configs(directory)[0]
    result = fr.replay_dump(dump.read_bytes(), config)
    assert result.ball_points, "the synthetic ball must be tracked for this test to mean anything"
    labels = _labels_from_result(dump, result)
    scores = ls.score_labels(labels, result)
    assert ls.dump_score(scores) == pytest.approx(1.0)
    assert ls.check_labels(labels, scores) == []


@needs_compiler
def test_a_wrong_label_lowers_the_score_and_a_missing_one_fails_coverage(tmp_path):
    directory = _recordings_dir(tmp_path)
    dump, config = fr.recording_configs(directory)[0]
    result = fr.replay_dump(dump.read_bytes(), config)
    shifted = _labels_from_result(dump, result, shift=5.0)
    shifted_scores = ls.score_labels(shifted, result)
    assert ls.dump_score(shifted_scores) < 1.0
    assert ls.check_labels(shifted, shifted_scores)  # coverage collapsed
    tracked = {p.frame for p in result.ball_points}
    unseen = [f for f in range(60) if f not in tracked][: 3 * len(tracked)]
    missed = _labels_from_result(dump, result, unseen_ball_frames=unseen)
    missed_scores = ls.score_labels(missed, result)
    assert any("ball: coverage" in f for f in ls.check_labels(missed, missed_scores))
