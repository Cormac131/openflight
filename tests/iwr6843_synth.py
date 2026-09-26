"""Shared synthetic club-motion generator for club-path tests and golden vectors.

``synth_club_dump`` is used by both ``tests/test_iwr6843_club_path.py`` (as
its ``_synth_club`` fixture) and ``scripts/dev/generate_golden_vectors.py``
(to build the ``club/`` golden corpus). It used to be defined twice --
line-for-line identical except for the generator's copy dropping this
docstring's derivation. Keeping one copy means a correction to the model
(or its documented reasoning) can never silently diverge between the test
suite and the corpus that verifies the C port.
"""

from __future__ import annotations

import math

import numpy as np

from openflight.iwr6843 import club, doa
from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16, pack_dump
from openflight.iwr6843.lcmf import TX2_LOOP_PERIOD_S

FRAME_PERIOD_S = 4e-3
# The fixture's club crosses the tee at this instant by default;
# estimate_club_path is told the impact time rather than inferring it from a
# ring slot.
IMPACT_S = club.PRE_IMPACT_FRAMES * FRAME_PERIOD_S
CLUB_SPEED_MS = 22.0
OPS_CLUB_MPH = CLUB_SPEED_MS * 2.23694


def synth_club_dump(
    path_deg,
    *,
    club_speed_ms=CLUB_SPEED_MS,
    tee_range_m=1.372,
    n_samples=128,
    n_frames=18,
    loops=12,
    t_impact_s=None,
    phase_bias_rad=0.0,
):
    """A club head on a straight line through the tee at the moment of impact.

    Built in Cartesian space, not by asserting an azimuth rate directly:
    position(t) = tee_position + (t - t_impact) * velocity, where velocity
    has magnitude club_speed_ms and direction path_deg off the target line
    (the boresight / x-axis). Range and azimuth at each (frame, loop) are the
    exact polar coordinates of that position -- range becomes the bin index,
    azimuth becomes the TX2-vs-(TX1,TX3) phase (phase = -pi*sin(az)), which
    estimate_club_path inverts exactly with arcsin. The raw TX2/TX3 values
    also carry the TDM-Doppler phase that tx2_phase_at's own motion
    correction expects to remove (using this same target's true local radial
    speed), so the round trip is exact modulo the estimator's linear-fit
    approximation of a rate that is not actually constant along a straight
    line -- that residual is the thing under test.

    Every TX block within a loop also carries the target's true round-trip
    phase 4*pi*range(t)/lambda. This is common to TX1/TX2/TX3 within one
    loop, so it exactly cancels in tx2_phase_at's conj(reference)*tx2
    difference and never touches the recovered azimuth. But it is NOT
    optional: without it, a slowly-walking target is nearly bit-for-bit
    identical across the loops of one burst, and burst-scope MTI (subtract
    each bin's mean over the burst's loops) fully cancels it -- exactly the
    "static argmax never sees the ball" problem tracking.py's docstring
    describes, here hitting the azimuth channel instead of the range one.
    A real target survives MTI because it keeps moving sub-bin during the
    burst, which is precisely this phase term.

    Impact happens at ``t_impact_s`` from the oldest retained frame,
    defaulting to IMPACT_S. It is a free parameter because impact's real
    position in the ring varies shot to shot (the freeze is requested by a
    UART command), which is exactly what estimate_club_path must be told
    rather than assume.

    The dump MUST declare sample_fmt=SAMPLE_RANGE_FFT_IQ16. Writing an
    amplitude spike at a range bin produces range-domain data, and this
    firmware emits range snapshots in the field, so is_range_snapshot() must
    be True. Left as raw-ADC, mti_filter FFTs the spike a second time; a
    time-domain impulse has flat magnitude across every bin, so no range peak
    survives and the tracker finds nothing (SNR ~2.3 against snr_min 4.0).

    ``n_frames``/``loops`` are parameterised (defaulting to the 18/12 every
    existing club test relies on) so callers that need a shorter ring --
    e.g. a "short capture" golden case -- can shrink the movie without
    duplicating the geometry/phase derivation above.
    """
    n_tx, n_rx = 3, 4
    res = 6.0 / n_samples
    t_impact = IMPACT_S if t_impact_s is None else t_impact_s
    path_rad = math.radians(path_deg)
    v_x = club_speed_ms * math.cos(path_rad)
    v_y = club_speed_ms * math.sin(path_rad)
    # TDM offset of each TX's chirp from TX1's, within one loop: TX1 is the
    # reference (offset 0), TX2 (the modulated element) trails by TDM_TAU_S,
    # TX3 trails by TX2_VERTICAL_TDM_TAU_S (see doa.tx2_phase_at). tdm_sign
    # is fixed at +1 to match every call in this module.
    tdm_offsets = (0.0, doa.TDM_TAU_S, doa.TX2_VERTICAL_TDM_TAU_S)
    cube = np.zeros((n_frames, loops * n_tx, n_rx, n_samples), dtype=complex)
    for frame in range(n_frames):
        for loop in range(loops):
            t = frame * FRAME_PERIOD_S + loop * TX2_LOOP_PERIOD_S
            s = t - t_impact
            x = tee_range_m + s * v_x
            y = s * v_y
            range_m = math.hypot(x, y)
            bin_at = int(range_m / res)
            if not 0 <= bin_at < n_samples:
                continue
            az_rad = math.atan2(y, x)
            # The validated enclosure has TX above RX. After that rotation,
            # TX2 is physically left of the TX1/TX3 phase center, so a target
            # to the right produces a negative residual phase.
            phase_az = -math.pi * math.sin(az_rad) + phase_bias_rad
            v_r = (x * v_x + y * v_y) / range_m  # true local radial speed
            doppler_phase = 4.0 * math.pi * range_m / doa.LAM
            for tx in range(n_tx):
                amp = 1000.0
                az_factor = 1.0 if tx != 1 else np.exp(1j * phase_az)
                tdm_phase = 4.0 * np.pi * v_r * tdm_offsets[tx] / doa.LAM
                value = amp * az_factor * np.exp(1j * (tdm_phase + doppler_phase))
                cube[frame, loop * n_tx + tx, :, bin_at] = value
    return pack_dump(
        cube,
        n_tx=n_tx,
        version=3,
        frame_period_us=int(FRAME_PERIOD_S * 1e6),
        trigger_frame=0,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16,
    )
