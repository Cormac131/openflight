"""The phase-0 baseline extractor: session JSONL in, one row per shot out."""

from __future__ import annotations

import csv
import json

import pytest

from openflight.iwr6843.baseline import (
    BaselineRow,
    collect_baseline,
    collect_sessions,
    summarize,
    write_csv,
)


def _metric(value, confidence=0.8, usable=True):
    return {"value": value, "confidence": confidence, "usable": usable, "label": "MEASURED"}


def _session(tmp_path, name="session_20260927_120000.jsonl"):
    entries = [
        {
            "type": "session_start",
            "config": {"iwr6843": {"enabled": True, "full_capture": False}},
        },
        {
            "type": "shot_detected",
            "shot_number": 1,
            "timestamp": "2026-09-27T12:00:01",
            "club": "7_iron",
            "ball_speed_mph": 110.0,
            "club_speed_mph": 82.0,
            "spin_rpm": 6200,
            "pipeline_ms": {"total": 212.5},
            "iwr6843_onboard": {
                "verdict": "valid",
                "impact_source": "geometry",
                "impact_timestamp_us": 23000,
                "club_points": 7,
                "ball_points": 9,
                "smash": 1.34,
                "quality": ["ball_locked", "impact_geometric"],
                "metrics": {
                    "ball_speed": _metric(48.5, 0.9),
                    "vertical_launch": _metric(16.2),
                    "horizontal_launch": _metric(-1.1),
                    "club_speed": _metric(36.0, 0.7),
                    "club_path": _metric(2.4),
                    "angle_of_attack": _metric(None, 0.0, usable=False),
                    "spin_rate": _metric(None, 0.0, usable=False),
                    "spin_axis": _metric(None, 0.0, usable=False),
                    "impact_range": _metric(1.61, 0.5),
                },
            },
        },
        {
            "type": "iwr6843_capture",
            "shot_number": 1,
            "capture_bytes": 43000,
            "dump_duration_s": 0.9,
            "measurement": {"angle_deg": 15.8, "confidence": 0.77, "coherence": 0.91},
            "club_path": {
                "path_deg": 1.9,
                "status": "accepted",
                "candidate_attack_angle_deg": -3.2,
            },
        },
        {"type": "shot_detected", "shot_number": 2, "timestamp": "t2", "ball_speed_mph": 95.0},
        "not json at all",
    ]
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(e) if isinstance(e, dict) else e for e in entries) + "\n",
        encoding="utf-8",
    )
    return path


def test_rows_join_shots_with_captures_and_convert_units(tmp_path):
    rows = collect_baseline(_session(tmp_path), firmware_sha="abc1234")
    assert [r.shot_number for r in rows] == [1, 2]
    row = rows[0]
    assert row.session == "session_20260927_120000" and row.firmware_sha == "abc1234"
    assert row.capture_format == "iq16" and row.club == "7_iron"
    assert row.ops_ball_speed_mph == 110.0 and row.ops_club_speed_mph == 82.0
    assert row.onboard_verdict == "valid" and row.onboard_impact_source == "geometry"
    assert row.onboard_ball_speed_mph == pytest.approx(108.49, abs=0.01)
    assert row.onboard_ball_speed_confidence == 0.9
    assert row.onboard_club_speed_mph == pytest.approx(80.53, abs=0.01)
    assert row.onboard_vertical_launch_deg == 16.2 and row.onboard_horizontal_launch_deg == -1.1
    assert row.onboard_angle_of_attack_deg is None, "unusable metrics stay empty"
    assert row.onboard_quality == "ball_locked,impact_geometric"
    assert row.host_vertical_launch_deg == 15.8 and row.host_angle_coherence == 0.91
    assert row.host_club_path_deg == 1.9 and row.host_attack_angle_deg == -3.2
    assert row.capture_bytes == 43000 and row.pipeline_ms == 212.5
    assert row.ball_speed_delta_mph == pytest.approx(-1.51, abs=0.01)
    assert row.club_speed_delta_mph == pytest.approx(-1.47, abs=0.01)
    # A shot without a capture or onboard result still gets a row.
    bare = rows[1]
    assert bare.onboard_verdict is None and bare.capture_bytes is None
    assert bare.ball_speed_delta_mph is None


def test_csv_round_trip_and_summary(tmp_path):
    rows = collect_sessions([tmp_path.parent / tmp_path.name], firmware_sha=None)
    assert rows == []
    _session(tmp_path)
    rows = collect_sessions([tmp_path])
    out = tmp_path / "baseline.csv"
    assert write_csv(rows, out) == 2
    with out.open(encoding="utf-8") as handle:
        table = list(csv.DictReader(handle))
    assert len(table) == 2 and table[0]["onboard_verdict"] == "valid"
    assert set(table[0]) == {f for f in BaselineRow.__dataclass_fields__}
    summary = summarize(rows)
    assert summary["shots"] == 2 and summary["with_onboard_result"] == 1
    assert summary["valid"] == 1 and summary["ball_speed_mae_mph"] == pytest.approx(1.51, abs=0.01)
    assert summarize([])["ball_speed_mae_mph"] is None


def test_full_capture_sessions_are_labelled(tmp_path):
    path = tmp_path / "session_x.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "session_start",
                "config": {"iwr6843": {"enabled": True, "full_capture": True}},
            }
        )
        + "\n"
        + json.dumps({"type": "shot_detected", "shot_number": 1, "ball_speed_mph": 1.0})
        + "\n",
        encoding="utf-8",
    )
    assert collect_baseline(path)[0].capture_format == "iq16-full"
    path.write_text(
        json.dumps({"type": "session_start", "config": {"iwr6843": {"enabled": False}}})
        + "\n"
        + json.dumps({"type": "shot_detected", "shot_number": 1, "ball_speed_mph": 1.0})
        + "\n",
        encoding="utf-8",
    )
    assert collect_baseline(path)[0].capture_format is None
