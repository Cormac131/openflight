"""A replay reproduces a recording, so its defaults are the settings the
recordings were made with, not the Pi's current ones; each tracker's snr is
set on its own.

The trigger's snr (``triggerCfg``) and the ball tracker's (``trackCfg
ballSnr``) are separate settings. Without either a replay uses the
recordings' trigger snr (6) and the firmware's ball snr, and no tee band.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from iwr6843_synth import synth_shot_dump

from openflight.iwr6843 import firmware_host as fw, firmware_replay as fr
from openflight.iwr6843.monitor import SELF_TRIGGER_DEFAULT_SNR, TEE_BAND_DEFAULT_BINS
from openflight.iwr6843.self_trigger import FIRMWARE_BALL_DEFAULT_SNR

TEE_RANGE_M = 1.372
TEE_BIN = int(TEE_RANGE_M / (6.0 / 128))

needs_compiler = pytest.mark.skipif(
    fw.host_compiler() is None, reason="no C compiler for the firmware modules"
)


def test_replay_keeps_the_recordings_trigger_snr_while_the_pi_sends_its_own():
    assert fr.ReplayConfig(tee_bin=TEE_BIN).snr == fr.DEFAULT_SNR == 6.0
    assert SELF_TRIGGER_DEFAULT_SNR == 1.0


def test_ball_snr_defaults_to_the_recordings_while_the_board_uses_one():
    assert fr.ReplayConfig(tee_bin=TEE_BIN).ball_snr is None
    assert fr.DEFAULT_BALL_SNR == 3.0
    assert FIRMWARE_BALL_DEFAULT_SNR == 1.0


@pytest.mark.parametrize("ball_snr", [0.5, 0.0, -1.0, float("nan"), float("inf")])
def test_ball_snr_outside_the_firmware_limits_is_refused(ball_snr):
    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=TEE_RANGE_M)
    with pytest.raises(ValueError, match="ball snr"):
        fr.replay_dump(raw, fr.ReplayConfig(tee_bin=TEE_BIN, ball_snr=ball_snr))


@needs_compiler
def test_default_replay_has_no_band_as_the_recordings_were_made():
    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=TEE_RANGE_M)
    result = fr.replay_dump(raw, fr.ReplayConfig(tee_bin=TEE_BIN, dest_bin=TEE_BIN))
    assert result.band is None


@needs_compiler
def test_the_boards_default_width_places_a_band():
    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=TEE_RANGE_M)
    config = fr.ReplayConfig(tee_bin=TEE_BIN, dest_bin=TEE_BIN, band_bins=TEE_BAND_DEFAULT_BINS)
    assert fr.replay_dump(raw, config).band is not None


@needs_compiler
def test_band_zero_still_turns_the_band_off():
    raw = synth_shot_dump(ball_speed_ms=60.0, tee_range_m=TEE_RANGE_M)
    result = fr.replay_dump(raw, fr.ReplayConfig(tee_bin=TEE_BIN, dest_bin=TEE_BIN, band_bins=0.0))
    assert result.band is None


_RECORDINGS = fr.recording_configs(fr.RECORDINGS_DIR) if fr.RECORDINGS_DIR.exists() else []


@needs_compiler
@pytest.mark.skipif(not _RECORDINGS, reason="no recorded captures")
def test_ball_snr_is_the_ball_trackers_own_threshold():
    """None is the recordings' ball snr (3); a higher ball snr loses ball points,
    the highest all of them, without moving the trigger. (The synthetic shot
    is too clean for this: its floor is the clamp.)"""
    path, config = _RECORDINGS[0]
    raw = path.read_bytes()
    default = fr.replay_dump(raw, replace(config, ball_snr=None))
    explicit = fr.replay_dump(raw, replace(config, ball_snr=3.0))
    stricter = fr.replay_dump(raw, replace(config, ball_snr=20.0))
    deaf = fr.replay_dump(raw, replace(config, ball_snr=fr.BALL_SNR_MAX))

    assert default.ball_points, "the recorded ball is tracked at the default"
    assert [(p.frame, p.range_m) for p in explicit.ball_points] == [
        (p.frame, p.range_m) for p in default.ball_points
    ]
    assert len(stricter.ball_points) < len(default.ball_points)
    assert not deaf.ball_points
    assert deaf.fired_frame == default.fired_frame
