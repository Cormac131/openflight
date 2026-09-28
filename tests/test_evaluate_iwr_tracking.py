"""Tests for scripts/analysis/evaluate_iwr_tracking.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from iwr6843_synth import synth_shot_dump

from openflight.iwr6843 import firmware_host as fw

SCRIPT = Path(__file__).parents[1] / "scripts" / "analysis" / "evaluate_iwr_tracking.py"
BIN_M = 6.0 / 128


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("evaluate_iwr_tracking", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


def point(frame, range_bin):
    return SimpleNamespace(frame=frame, range_bin=range_bin)


def frame_of(frame, timestamp_us, bins):
    return SimpleNamespace(
        frame=frame,
        timestamp_us=timestamp_us,
        targets=[SimpleNamespace(range_bin=b) for b in bins],
    )


def test_club_verdict_reads_the_last_points_before_the_split(ev):
    assert ev.club_verdict([point(f, 30.0 + f) for f in range(6)], 6) == "club"
    assert ev.club_verdict([point(f, 43.5 + 0.1 * f) for f in range(6)], 6) == "stuck"
    assert ev.club_verdict([point(0, 30.0), point(1, 31.0)], 6) == "few"
    after = [point(f, 30.0 + f) for f in range(4)] + [point(f, 50.0) for f in range(4, 9)]
    assert ev.club_verdict(after, 4) == "club"  # points at or after the split are ignored
    assert ev.club_verdict([point(f, 30.0 + f) for f in range(6)], None) == "club"


def test_ball_verdict_is_fifteen_percent_either_side(ev):
    assert ev.ball_verdict(None, 40.0) == "none"
    assert ev.ball_verdict(46.0, 40.0) == "ok"
    assert ev.ball_verdict(34.0, 40.0) == "ok"
    assert ev.ball_verdict(46.1, 40.0) == "wrong"
    assert ev.ball_verdict(13.2, 33.9) == "wrong"


def test_ball_present_needs_three_consecutive_points_at_the_ops_speed(ev):
    step = 40.0 * 0.002 / BIN_M
    frames = [frame_of(k, 2000 * k, [44.0, 50.0 + step * k]) for k in range(1, 4)]
    assert ev.ball_present(frames, 40.0, BIN_M) is True
    assert ev.ball_present(frames[:2], 40.0, BIN_M) is False
    assert ev.ball_present(frames, 20.0, BIN_M) is False  # twice the speed OPS saw
    gap = [frames[0], frames[1], frame_of(4, 8000, [50.0 + step * 4])]
    assert ev.ball_present(gap, 40.0, BIN_M) is False  # frame 3 missing breaks the chain
    assert ev.ball_present([], 40.0, BIN_M) is False


def test_split_frame_prefers_the_recorded_freeze(ev):
    with_freeze = SimpleNamespace(config=SimpleNamespace(post_from_frame=14), fired_frame=11)
    gate_only = SimpleNamespace(config=SimpleNamespace(post_from_frame=None), fired_frame=11)
    no_fire = SimpleNamespace(config=SimpleNamespace(post_from_frame=None), fired_frame=None)
    assert ev.split_frame(with_freeze) == 14
    assert ev.split_frame(gate_only) == 12
    assert ev.split_frame(no_fire) is None


def _outcome(ev, club, ball, present):
    return ev.Outcome("x.l3dump", club, ball, present, None, 40.0)


def test_summarize_counts_every_category(ev):
    outcomes = [
        _outcome(ev, "club", "ok", True),
        _outcome(ev, "stuck", "none", False),
        _outcome(ev, "few", "wrong", True),
    ]
    assert ev.summarize(outcomes) == {
        "captures": 3,
        "club": {"club": 1, "stuck": 1, "few": 1},
        "ball": {"ok": 1, "wrong": 1, "none": 1},
        "ball_present": 2,
    }


def test_compare_names_each_regression(ev):
    base = {
        "captures": 93,
        "club": {"club": 55, "stuck": 32, "few": 6},
        "ball": {"ok": 15, "wrong": 70, "none": 8},
        "ball_present": 23,
    }
    same = json.loads(json.dumps(base))
    assert ev.compare(same, base) == []
    worse = json.loads(json.dumps(base))
    worse["club"]["club"] = 54
    worse["ball"]["ok"] = 14
    worse["ball"]["none"] = 10
    problems = ev.compare(worse, base)
    assert len(problems) == 3
    assert ev.compare(worse, base, allow_more_none=2) == problems[:2]
    other = json.loads(json.dumps(base))
    other["captures"] = 92
    assert ev.compare(other, base) == ["captures: 92 vs baseline 93"]


@pytest.mark.skipif(fw.host_compiler() is None, reason="no C compiler for the firmware modules")
def test_a_synthetic_shot_with_its_session_log_is_scored_end_to_end(ev, tmp_path):
    dumps = tmp_path / "iwr6843"
    dumps.mkdir()
    dump = dumps / "iwr6843_20990101_000000_000_001.l3dump"
    dump.write_bytes(
        synth_shot_dump(ball_speed_ms=60.0, vla_deg=12.0, hla_deg=0.0, tee_range_m=1.372)
    )
    rows = [
        {
            "type": "session_start",
            "trigger_type": "sound",
            "config": {"iwr6843": {"self_trigger": "triggerCfg 29 6.0 2"}},
        },
        {
            "type": "iwr6843_capture",
            "shot_number": 1,
            "capture_path": f"/home/pi/{dump.name}",
            "ball_speed_mph": 60.0 / 0.44704,
        },
    ]
    (tmp_path / "session_x.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    cases = list(ev.iter_cases([tmp_path]))
    assert len(cases) == 1 and cases[0].config.tee_bin == 29
    outcome = ev.evaluate(cases[0])
    assert (outcome.club, outcome.ball, outcome.ball_present) == ("club", "ok", True)
    assert ev.main([str(tmp_path), "--json", str(tmp_path / "out.json")]) == 0
    written = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert written["summary"]["captures"] == 1


def test_the_cli_passes_the_ball_search_through(ev, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ev, "iter_cases", lambda roots: iter([object()]))

    def fake_evaluate(case, *, lib=None, ball_hypotheses=None):
        seen.append(ball_hypotheses)
        return ev.Outcome("x", "club", "ok", True, 40.0, 40.0)

    monkeypatch.setattr(ev, "evaluate", fake_evaluate)
    assert ev.main([str(tmp_path), "--ball-hypotheses", "on"]) == 0
    assert ev.main([str(tmp_path), "--ball-hypotheses", "off"]) == 0
    assert ev.main([str(tmp_path)]) == 0
    assert seen == [True, False, None]
