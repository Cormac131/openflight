"""The constants sweep: coordinate descent over a score, exercised with a fake score."""

from __future__ import annotations

import json
import math

import pytest
from iwr6843_synth import synth_shot_dump

from openflight.iwr6843 import (
    constants_fit as cf,
    firmware_host as fw,
    firmware_replay as fr,
    labels as lb,
    tunables as tn,
)

T_INT = tn.Tunable("club", "maxMisses", "int", 0, 5, 1)
T_FLOAT = tn.Tunable("club", "gateBins", "float", 1.0, 6.0, 0.5)

needs_compiler = pytest.mark.skipif(
    fw.host_compiler() is None, reason="no C compiler for the firmware modules"
)
TEE_BIN = int(1.372 / (6.0 / 128))


def test_candidates_step_around_the_current_value_clipped_and_deduplicated():
    assert cf.candidate_values(T_INT, 2) == [0, 1, 2, 3, 4, 5]
    assert cf.candidate_values(T_INT, 1) == [0, 1, 2, 3, 4]
    assert cf.candidate_values(T_FLOAT, 3.0) == [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5]
    assert (
        1.0 in cf.candidate_values(T_FLOAT, 1.0) and min(cf.candidate_values(T_FLOAT, 1.0)) == 1.0
    )


def test_a_float32_default_appears_once_in_the_grid():
    current = 0.20000000298023224  # 0.2 read back from a C float
    values = cf.candidate_values(T_FLOAT, current)
    assert current in values
    assert len([v for v in values if abs(v - current) < 1e-6]) == 1


def _peak_at(target):
    """A score with a single peak: club.gateBins at ``target``, the other constants ignored."""

    def evaluate(overrides):
        return -abs(overrides.get("club.gateBins", 3.0) - target)

    return evaluate


def test_descent_walks_to_the_peak_and_reports_the_gain():
    defaults = {"club.gateBins": 3.0, "club.maxMisses": 2}
    rows = cf.coordinate_descent(_peak_at(4.5), [T_FLOAT, T_INT], defaults, passes=2)
    by_name = {r.name: r for r in rows}
    assert by_name["club.gateBins"].suggested == 4.5
    assert by_name["club.gateBins"].changed
    assert by_name["club.gateBins"].gain == pytest.approx(1.5)
    assert not by_name["club.maxMisses"].changed
    assert by_name["club.maxMisses"].gain == 0.0
    # every candidate scored the same for maxMisses: the optimum is completely flat
    assert by_name["club.maxMisses"].flat == len(by_name["club.maxMisses"].grid)


def test_ties_keep_the_default():
    rows = cf.coordinate_descent(lambda _o: 1.0, [T_INT], {"club.maxMisses": 2})
    assert rows[0].suggested == 2 and not rows[0].changed


def test_a_candidate_the_firmware_rejects_is_skipped_not_fatal():
    def evaluate(overrides):
        if overrides.get("club.maxMisses", 2) == 3:
            return -math.inf
        # 3 would be the peak; 4 is nearer than 2, so it is the best runnable value
        return -abs(overrides.get("club.maxMisses", 2) - 3.2)

    rows = cf.coordinate_descent(evaluate, [T_INT], {"club.maxMisses": 2})
    assert rows[0].suggested == 4  # 3 is unrunnable, 4 is the next best
    assert dict(rows[0].grid)[3] == -math.inf


def test_evaluations_are_cached_per_override_set():
    calls = []

    def evaluate(overrides):
        calls.append(dict(overrides))
        return 0.0

    cf.coordinate_descent(evaluate, [T_INT], {"club.maxMisses": 2}, passes=2)
    keys = [tuple(sorted(c.items())) for c in calls]
    assert len(keys) == len(set(keys))


def test_report_lists_each_constant_with_default_suggestion_gain_and_flatness():
    rows = cf.coordinate_descent(_peak_at(4.5), [T_FLOAT], {"club.gateBins": 3.0})
    text = cf.format_report(rows, baseline_score=0.5, final_score=0.7, n_dumps=3, n_points=41)
    assert "3 dumps" in text and "41" in text
    assert "club.gateBins" in text and "3.0" in text and "4.5" in text
    assert "0.500" in text and "0.700" in text


# --- the real replay wiring -----------------------------------------------------


def _synthetic_recording(tmp_path):
    """One synthetic dump, its manifest config, and labels equal to the firmware's own track."""
    dump = tmp_path / "synth.l3dump"
    dump.write_bytes(synth_shot_dump(ball_speed_ms=60.0, tee_range_m=1.372))
    (tmp_path / "manifest.json").write_text(
        json.dumps({"default": {"tee_bin": TEE_BIN, "dest_bin": TEE_BIN}}), encoding="utf-8"
    )
    _, config = fr.recording_configs(tmp_path)[0]
    raw = dump.read_bytes()
    result = fr.replay_dump(raw, config)
    assert result.ball_points, "the synthetic ball must be tracked for this test to mean anything"
    base = lb.empty_labels(dump)
    labels = lb.Labels(
        base.dump,
        base.dump_sha256,
        True,
        ball=tuple(lb.LabelPoint(p.frame, p.range_bin) for p in result.ball_points),
        club=tuple(lb.LabelPoint(p.frame, p.range_bin) for p in result.points),
    )
    return [(raw, config, labels)], config


@needs_compiler
def test_evaluate_recordings_replays_with_the_overrides(tmp_path):
    recordings, _ = _synthetic_recording(tmp_path)
    assert cf.evaluate_recordings(recordings, {}) == pytest.approx(1.0)
    # a gate this wide removes the synthetic ball's track, so the labelled ball is missed
    assert cf.evaluate_recordings(recordings, {"ball.originGateBins": 2.0}) < 1.0


@needs_compiler
def test_evaluate_recordings_returns_minus_inf_when_the_replay_rejects_the_settings(
    tmp_path, monkeypatch
):
    recordings, _ = _synthetic_recording(tmp_path)

    def reject(_raw, _config):
        raise ValueError("firmware rejected the configuration")

    monkeypatch.setattr(fr, "replay_dump", reject)
    assert cf.evaluate_recordings(recordings, {}) == -math.inf


def test_report_shows_float32_defaults_readably():
    rows = cf.coordinate_descent(lambda _o: 1.0, [T_FLOAT], {"club.gateBins": 0.20000000298023224})
    assert "0.2 " in cf.format_report(rows, 1.0, 1.0, 1, 1)
