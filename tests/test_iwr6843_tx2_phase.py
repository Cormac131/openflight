"""The TX2 phase helper is shared by the ball's horizontal proxy and club path.

Extracting it must not move the horizontal number: this test pins the helper's
contract, and test_iwr6843_pipeline.py pins the proxy's output.
"""

import numpy as np

from openflight.iwr6843 import doa


def _tdm(phase_offset_rad, *, n_frames=2, loops=2, n_rx=4, n_samples=8, n_tx=3):
    """TDM-split cube where TX2 leads the TX1/TX3 reference by a known phase."""
    tdm = np.zeros((n_frames, loops, n_tx, n_rx, n_samples), dtype=complex)
    tdm[:, :, 0, :, :] = 1.0 + 0j
    tdm[:, :, 2, :, :] = 1.0 + 0j
    tdm[:, :, 1, :, :] = np.exp(1j * phase_offset_rad)
    return tdm


def test_recovers_a_known_tx2_phase_offset():
    tdm = _tdm(0.4)
    result = doa.tx2_phase_at(tdm, 0, 0, 3, velocity_ms=0.0, tdm_sign=1, n_rx=4)
    assert result is not None
    phase, weight = result
    np.testing.assert_allclose(phase, 0.4, atol=1e-6)
    assert weight > 0.0


def test_zero_amplitude_returns_none():
    tdm = np.zeros((1, 1, 3, 4, 8), dtype=complex)
    assert doa.tx2_phase_at(tdm, 0, 0, 3, velocity_ms=0.0, tdm_sign=1, n_rx=4) is None


def test_circular_median_wraps():
    values = [3.0, -3.0, 3.1]
    median = doa.circular_median(values)
    assert abs(abs(median) - 3.05) < 0.2, "median must wrap, not average through zero"
