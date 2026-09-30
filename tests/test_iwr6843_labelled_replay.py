"""The firmware's tracks against the hand labels committed beside the recordings.

Every ``*.l3dump`` in ``tests/radar/recordings`` that has a reviewed
``<dump>.labels.json`` is replayed with its manifest configuration. The
firmware must cover the labelled frames within the file's tolerances, track
nothing on an object labelled empty, and not score below the committed
baseline. To accept a deliberate change run
``uv run python scripts/analysis/fit_constants.py --update-baseline``.
"""

from __future__ import annotations

import functools
from dataclasses import replace

import pytest

from openflight.iwr6843 import firmware_host as fw, firmware_replay as fr, label_scoring as ls
from openflight.iwr6843.monitor import SELF_TRIGGER_TEE_LEAD_BINS
from openflight.iwr6843.self_trigger import FIRMWARE_TRIGGER_DEFAULT_SNR, TEE_BAND_DEFAULT_BINS

needs_compiler = pytest.mark.skipif(
    fw.host_compiler() is None, reason="no C compiler for the firmware modules"
)

_RECORDINGS = ls.reviewed_recordings(fr.RECORDINGS_DIR) if fr.RECORDINGS_DIR.exists() else []
_BASELINE = ls.load_baseline(fr.RECORDINGS_DIR) if fr.RECORDINGS_DIR.exists() else {}


@needs_compiler
@pytest.mark.parametrize(
    ("path", "config", "labels"), _RECORDINGS, ids=[r[0].name for r in _RECORDINGS]
)
def test_firmware_tracks_match_the_labels(path, config, labels):
    result = fr.replay_dump(path.read_bytes(), config)
    scores = ls.score_labels(labels, result)
    failures = ls.check_against_baseline(path.name, labels, scores, _BASELINE)
    assert failures == [], f"{path.name}: {failures}; {scores}"


# The share of labelled swings the kiosk's self-trigger must fire on inside
# the launch window. 2026-09-30: aimed at the ball's range (less the lead) it
# fired within two frames of launch on 19 of 34, because the club's radar
# range at impact is 3-12 bins short of the ball's (median 7.4).
KIOSK_TRIGGER_MIN_SHARE = 0.85
# The window is lopsided on purpose. A fire up to EARLY_FRAMES before launch
# still records the launch and most of the flight in the 16 post frames
# (L3_DEFAULT_POST_FRAMES); a late fire loses ball frames. In the early
# 2026-08-09 captures the club is invisible (to the labeller too) for the 3-5
# frames before launch, so no rule that watches the club lands closer.
EARLY_FRAMES = 4
LATE_FRAMES = 2


def _kiosk_config(config: fr.ReplayConfig, ball_bin: int) -> fr.ReplayConfig:
    """What the kiosk sends for a ball at ``ball_bin``: ``triggerCfg`` aimed
    SELF_TRIGGER_TEE_LEAD_BINS short of it at the default snr, the default tee
    band, no locked-ball destination and no manifest overrides; nothing forces
    impact, so the self-trigger alone decides when the capture freezes."""
    return replace(
        config,
        tee_bin=ball_bin - SELF_TRIGGER_TEE_LEAD_BINS,
        dest_bin=None,
        snr=FIRMWARE_TRIGGER_DEFAULT_SNR,
        band_bins=TEE_BAND_DEFAULT_BINS,
        post_from_frame=None,
        overrides={},
    )


@functools.cache
def _kiosk_fire_offsets() -> tuple[tuple[str, int | None], ...]:
    """(dump, fired frame - labelled launch frame, None when it never fired)
    for every labelled swing replayed at the kiosk's settings. The ball's bin
    is its first labelled point (the tape a correctly measured tee gives), the
    launch frame that point's frame."""
    judged = []
    for path, config, labels in _RECORDINGS:
        if not labels.ball:
            continue
        launch = labels.ball[0]
        result = fr.replay_dump(
            path.read_bytes(), _kiosk_config(config, int(round(launch.range_bin)))
        )
        offset = None if result.fired_frame is None else result.fired_frame - launch.frame
        judged.append((path.name, offset))
    return tuple(judged)


@needs_compiler
def test_the_kiosk_self_trigger_fires_at_the_labelled_launch():
    judged = _kiosk_fire_offsets()
    assert len(judged) >= 30
    near = [
        name
        for name, offset in judged
        if offset is not None and -EARLY_FRAMES <= offset <= LATE_FRAMES
    ]
    missed = [(name, offset) for name, offset in judged if name not in near]
    assert len(near) >= KIOSK_TRIGGER_MIN_SHARE * len(judged), (
        f"fired from {EARLY_FRAMES} frames before to {LATE_FRAMES} after launch on "
        f"{len(near)}/{len(judged)}; missed (fire - launch frames): {missed}"
    )


# The ball tracker looks for the ball within 8 bins of its rest bin after the
# fire; at the labelled launch rates (1.5-3.6 bins a frame, median 2.8) three
# frames is as late as a fire can come and still hand it the ball.
LATEST_FIRE_FRAMES = 3


@needs_compiler
def test_every_labelled_swing_fires_at_the_kiosk_settings_before_the_ball_is_lost():
    """When the club rules miss (the club unseen before launch, as in the early
    2026-08-09 captures), the ball leaving still fires, late but in time."""
    judged = _kiosk_fire_offsets()
    unfired = [name for name, offset in judged if offset is None]
    too_late = [
        (name, offset)
        for name, offset in judged
        if offset is not None and offset > LATEST_FIRE_FRAMES
    ]
    assert unfired == [] and too_late == [], (
        f"never fired: {unfired}; fired more than {LATEST_FIRE_FRAMES} frames after "
        f"launch: {too_late}"
    )
