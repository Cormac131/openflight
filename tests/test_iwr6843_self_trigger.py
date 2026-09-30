"""The host side of the self-trigger: the shared settings and the floor sample.

The host ball-leave detector these tests once replayed over saved dumps was
removed on 2026-09-30; the board fires on the club track's range-only impact,
which test_iwr6843_firmware_replay.py replays.
"""

from __future__ import annotations

import numpy as np
import pytest

from openflight.iwr6843 import self_trigger as st
from openflight.iwr6843.self_trigger import (
    FLOOR_MARGIN,
    FLOOR_MIN_SAMPLES,
    FLOOR_PERCENTILE,
    level_above_floor,
    tee_power_from_stats,
)


def test_stats_line_yields_the_tee_residual():
    text = "frames=4 active=1\ntrig phase=tee-low tee=201000 latched=0 enabled=1\nDone\n"

    assert tee_power_from_stats(text) == 201000.0
    assert tee_power_from_stats("frames=0\nDone\n") is None
    assert tee_power_from_stats("trig phase=no-frame tee=0 latched=0 enabled=1") == 0.0


def test_level_above_floor_is_the_sample_p95_times_margin():
    samples = [100_000.0] * 19 + [200_000.0]

    floor, level = level_above_floor(samples)

    assert floor == pytest.approx(float(np.percentile(samples, FLOOR_PERCENTILE)))
    assert level == pytest.approx(floor * FLOOR_MARGIN)


def test_level_above_floor_ignores_zeros_and_requires_a_full_sample():
    with pytest.raises(ValueError, match="background samples"):
        level_above_floor([0.0] * 20)
    with pytest.raises(ValueError, match="background samples"):
        level_above_floor([180_000.0] * (FLOOR_MIN_SAMPLES - 1))


@pytest.mark.parametrize("snr", [0.5, float("nan"), 2.0e6])
def test_ball_snr_outside_what_the_firmware_takes_is_refused(snr):
    with pytest.raises(ValueError, match="ball snr"):
        st.check_ball_snr(snr)


def test_the_host_ball_leave_detector_is_gone():
    for gone in ("BallLeaveDetector", "TriggerObservation", "replay_dump", "PHASES"):
        assert not hasattr(st, gone), gone
