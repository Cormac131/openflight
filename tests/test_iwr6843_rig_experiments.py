"""Orientation and enclosure experiment matrices (rig_experiments.py)."""

from __future__ import annotations

import json

import pytest
from test_iwr6843_clutter_bench import metrics

from openflight.iwr6843 import rig_experiments as rig


class TestManifest:
    def test_loads(self, tmp_path):
        path = tmp_path / "rig.json"
        path.write_text(
            json.dumps(
                {
                    "a.l3dump": {"label": "yaw+10", "yaw_deg": 10, "pitch_deg": 0},
                    "b.l3dump": {"label": "B", "enclosure": "B"},
                }
            )
        )
        conditions = rig.load_rig_manifest(path)
        assert conditions["a.l3dump"].yaw_deg == 10
        assert conditions["b.l3dump"].enclosure == "B"

    @pytest.mark.parametrize(
        "content",
        [
            [],
            {"a": 3},
            {"a": {"label": "x", "roll_deg": 1}},
            {"a": {"label": ""}},
            {"a": {"label": "x", "enclosure": "D"}},
        ],
    )
    def test_rejects(self, tmp_path, content):
        path = tmp_path / "rig.json"
        path.write_text(json.dumps(content))
        with pytest.raises(ValueError):
            rig.load_rig_manifest(path)


def orientation_set():
    conditions = {
        "a1": rig.RigCondition("0", 0.0, 0.0),
        "a2": rig.RigCondition("0", 0.0, 0.0),
        "b1": rig.RigCondition("+10", 10.0, 0.0),
    }
    captured = [
        metrics("a1", club_scr_db=2.0, ball_scr_db=-12.0, hotspot_db=82.0),
        metrics("a2", club_scr_db=4.0, ball_scr_db=-10.0, hotspot_db=80.0, triggered=False),
        metrics("b1", club_scr_db=6.0, ball_scr_db=-4.0, hotspot_db=75.0, ball_speed_mps=None),
        metrics("stray"),
    ]
    return captured, conditions


class TestMatrix:
    def test_groups_by_label_with_medians_and_rates(self):
        rows = rig.rig_matrix(*orientation_set())
        assert [r.label for r in rows] == ["0", "+10"]
        zero, ten = rows
        assert zero.captures == 2 and zero.club_scr_db == 3.0 and zero.golfer_db == 81.0
        assert zero.trigger_rate == 0.5 and zero.tracking_rate == 1.0
        assert ten.tracking_rate == 0.0 and ten.yaw_deg == 10.0
        assert zero.combined_scr_db == pytest.approx(-4.0)

    def test_best_orientation_maximises_combined_scr(self):
        rows = rig.rig_matrix(*orientation_set())
        assert rig.best_orientation(rows).label == "+10"
        assert rig.best_orientation([]) is None

    def test_format(self):
        table = rig.format_matrix(rig.rig_matrix(*orientation_set()))
        lines = table.splitlines()
        assert lines[0].startswith("| Condition | Captures | Golfer dB")
        assert lines[2] == "| 0 | 2 | 81.0 | 3.0 | -11.0 | 50% | 100% |"


class TestEnclosure:
    def rows(self, a, b=None, c=None):
        conditions, captured = {}, []
        for name, value in (("A", a), ("B", b), ("C", c)):
            if value is None:
                continue
            conditions[name] = rig.RigCondition(name, enclosure=name)
            captured.append(metrics(name, hotspot_db=value))
        return rig.rig_matrix(captured, conditions)

    def test_multipath_when_the_enclosure_adds_to_the_hotspot(self):
        verdict = rig.enclosure_verdict(self.rows(70.0, 71.0, 76.5))
        assert verdict == "enclosure/multipath contribution: C (+6.5 dB) over A"

    def test_direct_return_when_it_does_not(self):
        assert rig.enclosure_verdict(self.rows(70.0, 72.0)).startswith("no enclosure contribution")

    def test_incomplete(self):
        assert rig.enclosure_verdict(self.rows(70.0)).startswith("incomplete")
        assert rig.enclosure_verdict(self.rows(None, 70.0)).startswith("incomplete")
