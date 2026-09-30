"""Golfer-clutter benchmark metrics and the Phase 1 acceptance (clutter_bench.py)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from iwr6843_synth import synth_shot_dump

from openflight.iwr6843 import clutter_bench as cb, firmware_replay as fr
from openflight.iwr6843.clutter_map import ClutterConfig


def metrics(name="a", **kwargs):
    base = dict(
        name=name,
        impact_frame=9,
        triggered=True,
        self_triggered=False,
        hotspot_bin=40,
        hotspot_db=80.0,
        hotspot_seen_db=80.0,
        club_points_pre=6,
        club_points_in_hotspot=1,
        club_points_post=2,
        club_longest_run=5,
        club_speed_mps=30.0,
        ball_first_frame=10,
        ball_points=8,
        ball_longest_run=8,
        ball_speed_mps=45.0,
        false_detections=10,
        club_scr_db=3.0,
        ball_scr_db=-10.0,
    )
    base.update(kwargs)
    return cb.CaptureMetrics(**base)


class TestHelpers:
    @pytest.mark.parametrize(
        "frames, expected", [([], 0), ([3], 1), ([1, 2, 3, 7, 8], 3), ([5, 4, 4, 6], 3)]
    )
    def test_longest_run(self, frames, expected):
        assert cb.longest_run(frames) == expected

    def test_frame_powers_marks_bins_outside_each_window(self):
        raw = synth_shot_dump(n_frames=4)
        powers = cb.frame_powers(raw, 128, "peak")
        assert powers.shape == (4, 128)
        assert not np.isnan(powers).any()

    def test_seen_powers_apply_the_gains_squared(self):
        recorded = np.ones((2, 6))
        result = fr.ReplayResult.__new__(fr.ReplayResult)
        result.clutter_seen_gains = ((1, (0.5, 1.0)), (0, ()))
        seen = cb.seen_powers(result, recorded)
        assert seen[0].tolist() == [1.0, 0.25, 1.0, 1.0, 1.0, 1.0]
        assert seen[1].tolist() == [1.0] * 6
        assert recorded[0, 1] == 1.0

    def test_seen_powers_without_suppression_are_the_recorded(self):
        recorded = np.ones((1, 3))
        result = fr.ReplayResult.__new__(fr.ReplayResult)
        result.clutter_seen_gains = ()
        assert cb.seen_powers(result, recorded) is recorded


@pytest.fixture(scope="module")
def replayed():
    raw = synth_shot_dump(
        n_frames=24, t_impact_s=12 * 4e-3, golfer_bin=26, golfer_amp=3000.0, ball_speed_ms=45.0
    )
    return raw, fr.ReplayConfig(tee_bin=29, post_from_frame=12)


class TestCaptureMetrics:
    def test_reads_the_golfer_as_the_hotspot(self, replayed):
        raw, config = replayed
        m = cb.capture_metrics("synth", raw, fr.replay_dump(raw, config))
        assert m.hotspot_bin == 26
        assert m.hotspot_db == pytest.approx(m.hotspot_seen_db)
        assert m.impact_frame == 12 and m.triggered
        assert m.club_points_pre >= 5 and m.ball_points >= 5
        assert m.ball_first_frame >= 12
        assert m.ball_speed_mps == pytest.approx(45.0 * np.cos(np.radians(12.0)), rel=0.1)
        assert m.club_label_coverage is None

    def test_suppression_lowers_what_the_trackers_see(self, replayed):
        raw, config = replayed
        config = replace(config, clutter=ClutterConfig(beta=1.0, min_updates=2))
        m = cb.capture_metrics("synth", raw, fr.replay_dump(raw, config))
        assert m.hotspot_seen_db < m.hotspot_db - 3.0

    def test_labels_are_scored_when_reviewed(self, replayed, tmp_path):
        from openflight.iwr6843.labels import (  # pylint: disable=import-outside-toplevel
            LabelPoint,
            Labels,
        )

        raw, config = replayed
        result = fr.replay_dump(raw, config)
        labels = Labels(
            dump="synth",
            dump_sha256="x",
            reviewed=True,
            ball=tuple(LabelPoint(p.frame, p.range_bin) for p in result.ball_points),
        )
        m = cb.capture_metrics("synth", raw, result, labels=labels)
        assert m.ball_label_coverage == 1.0
        assert m.club_label_coverage is None
        unreviewed = cb.capture_metrics(
            "synth", raw, result, labels=replace(labels, reviewed=False)
        )
        assert unreviewed.ball_label_coverage is None


class TestSummaryAndAcceptance:
    def test_summarize(self):
        out = cb.summarize([metrics("a"), metrics("b", ball_speed_mps=None, ball_points=0)])
        assert out["captures"] == 2 and out["with_launch"] == 1 and out["with_ball"] == 1
        assert out["club_points_pre"] == 12
        assert out["mean_club_label_score"] is None

    def test_passes(self):
        reference = [metrics("a"), metrics("b")]
        candidate = [metrics("a", hotspot_seen_db=75.0), metrics("b", hotspot_seen_db=76.0)]
        assert cb.acceptance(candidate, reference) == []

    def test_each_failure_is_reported(self):
        reference = [metrics("a"), metrics("b")]
        candidate = [
            metrics("a", hotspot_seen_db=79.0, club_points_pre=5),
            metrics("b", hotspot_seen_db=79.0, ball_first_frame=12, triggered=False),
        ]
        problems = cb.acceptance(candidate, reference)
        assert problems[0].startswith("hotspot drop 1.0 dB")
        assert "a: club points 5 < 6" in problems
        assert "b: first ball frame 12 later than 10" in problems
        assert "b: no longer triggers" in problems

    def test_a_lost_ball_is_later(self):
        problems = cb.acceptance(
            [metrics("a", hotspot_seen_db=70.0, ball_first_frame=None)], [metrics("a")]
        )
        assert problems == ["a: first ball frame None later than 10"]

    def test_captures_must_match(self):
        with pytest.raises(ValueError):
            cb.acceptance([metrics("a")], [metrics("b")])


RECORDED = fr.recording_configs()


@pytest.mark.skipif(not RECORDED, reason="no committed recordings")
def test_the_clutter_map_recovers_more_balls_on_the_recordings():
    """The Phase 1 result the design doc records (2026-09-30): the median map
    at beta 0.8 gives every committed recording a launch and raises the ball
    label coverage, without costing the club more than one point anywhere."""
    reference = [
        cb.benchmark_file(p, replace(c, clutter=ClutterConfig(beta=0.0))) for p, c in RECORDED
    ]
    suppressed = [
        cb.benchmark_file(p, replace(c, clutter=ClutterConfig(beta=0.8))) for p, c in RECORDED
    ]
    before, after = cb.summarize(reference), cb.summarize(suppressed)
    assert after["with_launch"] == after["captures"] > before["with_launch"]
    assert after["mean_ball_label_coverage"] > before["mean_ball_label_coverage"] + 0.1
    assert after["club_points_pre"] >= before["club_points_pre"]
    for c, r in zip(suppressed, reference, strict=True):
        assert c.club_points_pre >= r.club_points_pre - 1, c.name
