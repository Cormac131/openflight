"""Tests for offline replay of saved camera clips."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from camera_frames import (
    BALL,
    BALL_RADIUS,
    BALL_X,
    BALL_Y,
    EMPTY,
    FRAME_NS,
    OCCLUDED,
    FrameFactory,
    expand,
)

from openflight.camera.address_replay import (
    ClipReplayResult,
    fixed_ball_acquirer,
    replay_clip,
    replay_frames,
    summarize,
)
from openflight.camera.address_trigger import BallCandidate

REPO_ROOT = Path(__file__).resolve().parents[1]
BALL_CANDIDATE = BallCandidate(BALL_X, BALL_Y, BALL_RADIUS)


def render(steps):
    factory = FrameFactory()
    frames = np.stack([factory.render(scene) for scene in expand(steps)])
    sensor_ns = np.arange(len(frames), dtype=np.int64) * FRAME_NS + 5_000_000_000
    return frames, sensor_ns


SHOT = [(BALL, 40), (OCCLUDED, 1), (EMPTY, 15)]
STATIC = [(BALL, 56)]


def write_clip(path: Path, steps, *, sound_index: int) -> Path:
    frames, sensor_ns = render(steps)
    path.mkdir(parents=True)
    np.savez(
        path / "frames.npz",
        frames=frames,
        sensor_timestamp_ns=sensor_ns,
        host_timestamp_ns=sensor_ns,
        exposure_us=np.full(len(frames), 1000, dtype=np.int32),
        analogue_gain=np.full(len(frames), 2.0, dtype=np.float32),
        pre_trigger_count=np.int32(sound_index + 1),
        trigger_host_timestamp_ns=np.int64(0),
        trigger_epoch_timestamp=np.float64(0),
    )
    return path / "frames.npz"


def test_replay_frames_triggers_and_measures_delta():
    frames, sensor_ns = render(SHOT)
    result = replay_frames(
        frames,
        sensor_ns,
        acquire_fn=fixed_ball_acquirer(BALL_CANDIDATE),
        sound_trigger_index=40,
    )
    assert result.locked and result.triggered
    # Impact midpoint between frame 39 (last present) and 40 (club over ball).
    assert result.delta_ms == pytest.approx(-FRAME_NS / 2 / 1e6)
    assert result.impact_uncertainty_ms == pytest.approx(FRAME_NS / 2 / 1e6)
    assert "trigger" in result.events


def test_static_ball_does_not_trigger():
    frames, sensor_ns = render(STATIC)
    result = replay_frames(frames, sensor_ns, acquire_fn=fixed_ball_acquirer(BALL_CANDIDATE))
    assert result.locked and not result.triggered
    assert result.delta_ms is None


def test_no_ball_never_locks():
    frames, sensor_ns = render([(EMPTY, 30)])
    result = replay_frames(frames, sensor_ns, acquire_fn=lambda _f: None)
    assert not result.locked and not result.triggered


def test_rejects_mismatched_inputs():
    frames, sensor_ns = render([(BALL, 3)])
    with pytest.raises(ValueError):
        replay_frames(frames, sensor_ns[:2], acquire_fn=lambda _f: None)
    with pytest.raises(ValueError):
        replay_frames(frames[0], sensor_ns[:1], acquire_fn=lambda _f: None)


def test_replay_clip_reads_saved_format(tmp_path):
    clip = write_clip(tmp_path / "camera_1", SHOT, sound_index=40)
    result = replay_clip(clip, acquire_fn=fixed_ball_acquirer(BALL_CANDIDATE))
    assert result.triggered
    assert result.delta_ms == pytest.approx(-FRAME_NS / 2 / 1e6)
    assert result.path == str(clip)


def test_summary_lists_misses_and_unlocked():
    results = [
        ClipReplayResult("a", 10, True, True, -1.0, 1.7, ()),
        ClipReplayResult("b", 10, True, False, None, None, ()),
        ClipReplayResult("c", 10, False, False, None, None, ()),
        ClipReplayResult("d", 10, True, True, 3.0, 1.7, ()),
    ]
    summary = summarize(results)
    assert summary["clips"] == 4
    assert summary["triggered"] == 2
    assert summary["missed"] == ["b"]
    assert summary["not_locked"] == ["c"]
    assert summary["max_abs_delta_ms"] == 3.0


def test_summary_of_nothing():
    assert summarize([])["median_delta_ms"] is None


def test_cli_reports_json(tmp_path):
    write_clip(tmp_path / "shot", SHOT, sound_index=40)
    write_clip(tmp_path / "static", STATIC, sound_index=40)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/vision/replay_address_trigger.py",
            str(tmp_path),
            "--ball",
            f"{BALL_X},{BALL_Y},{BALL_RADIUS}",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["summary"]["clips"] == 2
    assert report["summary"]["triggered"] == 1
    assert report["summary"]["missed"] == [str(tmp_path / "static" / "frames.npz")]


def test_cli_without_clips_fails(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/vision/replay_address_trigger.py",
            str(tmp_path),
            "--ball",
            "1,1,5",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "No frames.npz clips" in result.stderr
