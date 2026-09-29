"""Tests for IWR6843 angle estimation, firmware/iwr6843/l3_angle.c.

Snapshots are synthesised from the same array model the host uses
(``music.steer`` for the 8-element elevation ULA, ``doa`` for the TX1
azimuth baseline and TDM phases), so the C estimator is checked against
the Python conventions it must share, then against a calibration with
per-element phase errors, a moving target whose TDM phase must be
resolved through the Doppler alias, and the reflector positions the
roadmap names (0, +/-10, +/-20 degrees).
"""

from __future__ import annotations

import ctypes
import math

import numpy as np
import pytest

from openflight.iwr6843 import doa, firmware_host as fw
from openflight.iwr6843.music import LAM, est_bartlett, steer

DEG = math.pi / 180.0
TAU_S = 45e-6
NRX = 4


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def identity_cal(lib) -> fw.RadarCal:
    c = fw.RadarCal()
    lib.l3_cal_identity(ctypes.byref(c), 8)
    return c


def synth_snapshot(
    lib,
    *,
    az_deg: float,
    el_deg: float,
    ntx: int = 3,
    v_radial: float = 0.0,
    amp: float = 1000.0,
    element_phase_rad: np.ndarray | None = None,
    noise: float = 0.0,
    seed: int = 0,
) -> fw.AngleSnapshot:
    """Channels of a point target as the firmware would hand them over.

    The elevation array in physical order is steer(el) over 8 elements; the
    logical [txA.rx, txB.rx] vector is that reversed. TX1 carries the azimuth
    phase -pi sin(az) (doa.tx2_axis_angle_to_phase_rad, negated for the
    board's TX1-left geometry). Each TX block trails the previous by tau, so
    the TDM phase 4 pi v tau / lambda multiplies by the TX index.
    """
    rng = np.random.default_rng(seed)
    physical = amp * steer(el_deg * DEG, 2 * NRX)
    if element_phase_rad is not None:
        physical = physical * np.exp(1j * element_phase_rad)
    logical = physical[::-1]  # [txA.rx0..3, txB.rx0..3]
    chirp_phase = 4.0 * math.pi * v_radial * TAU_S / LAM
    az_phase = -doa.tx2_axis_angle_to_phase_rad(az_deg * DEG)
    snap = fw.AngleSnapshot()
    lib.l3_angle_snapshot_init(ctypes.byref(snap), ntx, NRX)
    tx_b = 2 if ntx == 3 else 1
    for tx in range(ntx):
        for rx in range(NRX):
            if tx == 0:
                value = logical[rx]
            elif tx == tx_b:
                value = logical[NRX + rx]
            else:
                value = 0.5 * (logical[rx] + logical[NRX + rx]) * np.exp(1j * az_phase)
            value = value * np.exp(1j * chirp_phase * tx)
            value = value + noise * (rng.normal() + 1j * rng.normal())
            snap.channel[tx * NRX + rx] = fw.Cpx(float(value.real), float(value.imag))
    per_loop = chirp_phase * ntx
    snap.lag1PhaseRad = math.atan2(math.sin(per_loop), math.cos(per_loop))
    snap.radialVelocityMps = v_radial
    snap.chirpPeriodS = TAU_S
    return snap


def estimate(lib, snap, cal=None):
    cal = cal or identity_cal(lib)
    obs = fw.AngleObs()
    ok = lib.l3_angle_estimate(ctypes.byref(cal), ctypes.byref(snap), ctypes.byref(obs))
    return ok, obs


def test_bartlett_matches_the_host_beamformer_on_a_steered_snapshot(lib):
    for theta_deg in (-25.0, -10.0, 0.0, 7.3, 20.0):
        x = 500.0 * steer(theta_deg * DEG, 8)
        elements = (fw.Cpx * 8)(*(fw.Cpx(float(v.real), float(v.imag)) for v in x))
        ratio = ctypes.c_float()
        theta = lib.l3_angle_bartlett(elements, 8, ctypes.byref(ratio))
        assert theta / DEG == pytest.approx(theta_deg, abs=0.15)
        assert theta / DEG == pytest.approx(math.degrees(est_bartlett(x)), abs=0.3)
        assert ratio.value > 4.0, "a single clean source stands well above the mean"


@pytest.mark.parametrize("az_deg", [0.0, 10.0, -10.0, 20.0, -20.0])
@pytest.mark.parametrize("el_deg", [0.0, -8.0, 12.0])
def test_reflector_positions_are_recovered_with_the_documented_signs(lib, az_deg, el_deg):
    """The roadmap's reflector test: 0, +/-10, +/-20 degrees, at known heights."""
    ok, obs = estimate(lib, synth_snapshot(lib, az_deg=az_deg, el_deg=el_deg))
    assert ok == 1 and obs.azimuthValid and obs.elevationValid
    assert obs.azimuthRad / DEG == pytest.approx(az_deg, abs=0.2)
    assert obs.elevationRad / DEG == pytest.approx(el_deg, abs=0.2)
    assert obs.azimuthCoherence == pytest.approx(1.0, abs=1e-3)


def test_a_target_to_the_right_reads_a_negative_tx1_phase_and_positive_azimuth(lib):
    """The board's sign: TX1 is physically left of the pair centre (club.py)."""
    snap = synth_snapshot(lib, az_deg=15.0, el_deg=0.0)
    tx0 = complex(snap.channel[0].re, snap.channel[0].im)
    tx1 = complex(snap.channel[NRX].re, snap.channel[NRX].im)
    assert np.angle(tx1 * np.conj(tx0)) < 0
    _, obs = estimate(lib, snap)
    assert obs.azimuthRad > 0


def test_two_tx_snapshots_give_elevation_only(lib):
    ok, obs = estimate(lib, synth_snapshot(lib, az_deg=0.0, el_deg=-6.0, ntx=2))
    assert ok == 1 and obs.elevationValid and not obs.azimuthValid
    assert obs.elevationRad / DEG == pytest.approx(-6.0, abs=0.2)
    assert obs.azimuthCoherence == 0.0


def test_too_few_channels_are_refused(lib):
    snap = fw.AngleSnapshot()
    lib.l3_angle_snapshot_init(ctypes.byref(snap), 1, 4)
    ok, obs = estimate(lib, snap)
    assert ok == 0 and not obs.azimuthValid and not obs.elevationValid
    lib.l3_angle_snapshot_init(ctypes.byref(snap), 3, 1)
    ok, _ = estimate(lib, snap)
    assert ok == 0


def test_snapshot_init_clamps_dimensions_and_sets_the_shipped_chirp_period(lib):
    snap = fw.AngleSnapshot()
    lib.l3_angle_snapshot_init(ctypes.byref(snap), 9, 9)
    assert (snap.ntx, snap.nrx) == (3, 4)
    assert snap.chirpPeriodS == pytest.approx(45e-6)


# --- TDM motion phase ---------------


def test_motion_phase_is_four_pi_v_t_over_lambda(lib):
    assert lib.l3_angle_motion_phase(30.0, 45e-6) == pytest.approx(
        4 * math.pi * 30.0 * 45e-6 / 0.00484, rel=1e-6
    )
    assert lib.l3_angle_motion_phase(-30.0, 45e-6) == pytest.approx(
        -4 * math.pi * 30.0 * 45e-6 / 0.00484, rel=1e-6
    )


@pytest.mark.parametrize("v_radial", [0.0, 3.0, -8.0, 22.0, 35.0, -30.0])
def test_tdm_phase_is_resolved_through_the_doppler_alias_from_the_range_rate(lib, v_radial):
    """Per chirp, 35 m/s is 4.1 rad: the aliased per-loop phase alone is three
    ways ambiguous, and the coarse range-rate velocity picks the right one."""
    snap = synth_snapshot(lib, az_deg=12.0, el_deg=-5.0, v_radial=v_radial)
    expected = 4.0 * math.pi * v_radial * TAU_S / LAM
    psi = lib.l3_angle_chirp_phase(snap.lag1PhaseRad, 3, v_radial, TAU_S)
    assert psi == pytest.approx(expected, abs=1e-3)
    _, obs = estimate(lib, snap)
    assert obs.chirpPhaseRad == pytest.approx(expected, abs=1e-3)
    assert obs.azimuthRad / DEG == pytest.approx(12.0, abs=0.3)
    assert obs.elevationRad / DEG == pytest.approx(-5.0, abs=0.3)


def test_a_coarse_velocity_off_by_a_few_metres_per_second_still_picks_the_right_alias(lib):
    v_true = 30.0
    snap = synth_snapshot(lib, az_deg=0.0, el_deg=0.0, v_radial=v_true)
    snap.radialVelocityMps = v_true - 4.0  # range-rate over two frames is that rough
    _, obs = estimate(lib, snap)
    assert obs.chirpPhaseRad == pytest.approx(4.0 * math.pi * v_true * TAU_S / LAM, abs=1e-3)
    assert obs.azimuthRad / DEG == pytest.approx(0.0, abs=0.3)


def test_uncompensated_tdm_phase_would_corrupt_azimuth(lib):
    """Why the correction exists: without it a 22 m/s club reads far off."""
    snap = synth_snapshot(lib, az_deg=0.0, el_deg=0.0, v_radial=22.0)
    snap.lag1PhaseRad = 0.0
    snap.radialVelocityMps = 0.0
    _, obs = estimate(lib, snap)
    assert abs(obs.azimuthRad / DEG) > 5.0


def test_element_corrections_apply_in_physical_order_after_the_flip(lib):
    """A per-element phase error is undone by the matching correction; the
    same correction applied to the unflipped order would not cancel it."""
    rng = np.random.default_rng(7)
    errors = rng.uniform(-0.6, 0.6, size=8)
    snap = synth_snapshot(lib, az_deg=0.0, el_deg=9.0, element_phase_rad=errors)
    _, uncorrected = estimate(lib, snap)
    cal = identity_cal(lib)
    for m in range(8):
        cal.correctionRe[m] = math.cos(-errors[m])
        cal.correctionIm[m] = math.sin(-errors[m])
    _, corrected = estimate(lib, snap, cal)
    assert corrected.elevationRad / DEG == pytest.approx(9.0, abs=0.2)
    assert corrected.elevationPeakRatio > uncorrected.elevationPeakRatio
    reversed_cal = identity_cal(lib)
    for m in range(8):
        reversed_cal.correctionRe[m] = math.cos(-errors[7 - m])
        reversed_cal.correctionIm[m] = math.sin(-errors[7 - m])
    _, wrong_order = estimate(lib, snap, reversed_cal)
    assert wrong_order.elevationPeakRatio < corrected.elevationPeakRatio


def test_baseline_offsets_are_subtracted_from_the_measured_angles(lib):
    """The electrical zero of each baseline, as the corner reflector solve
    reports it: the azimuth offset is a TX1 phase (removed before the sign
    flip), the elevation offset an angle."""
    cal = identity_cal(lib)
    # A boresight reflector that reads 3 degrees right has a TX1 phase of
    # -pi sin(3 deg); storing that phase as the offset zeroes it.
    cal.azimuthOffsetRad = -doa.tx2_axis_angle_to_phase_rad(3.0 * DEG)
    cal.elevationOffsetRad = 2.0 * DEG
    _, obs = estimate(lib, synth_snapshot(lib, az_deg=3.0, el_deg=2.0), cal)
    assert obs.azimuthRad / DEG == pytest.approx(0.0, abs=0.2)
    assert obs.elevationRad / DEG == pytest.approx(0.0, abs=0.2)


def test_noise_lowers_coherence_and_peak_ratio_but_keeps_the_angles(lib):
    clean = estimate(lib, synth_snapshot(lib, az_deg=5.0, el_deg=-3.0))[1]
    noisy = estimate(lib, synth_snapshot(lib, az_deg=5.0, el_deg=-3.0, noise=150.0, seed=3))[1]
    assert noisy.azimuthCoherence < clean.azimuthCoherence
    assert noisy.elevationPeakRatio < clean.elevationPeakRatio
    assert noisy.azimuthRad / DEG == pytest.approx(5.0, abs=2.0)
    assert noisy.elevationRad / DEG == pytest.approx(-3.0, abs=2.0)


def test_format_prints_degrees_quality_and_validity(lib):
    _, obs = estimate(lib, synth_snapshot(lib, az_deg=10.0, el_deg=-5.0, v_radial=10.0))
    text = fw.c_text(lib.l3_angle_format, ctypes.byref(obs))
    assert text.startswith("angle az=")
    assert " el=-" in text and " coh=1.00" in text and " valid=ae" in text
    _, only_el = estimate(lib, synth_snapshot(lib, az_deg=0.0, el_deg=0.0, ntx=2))
    assert fw.c_text(lib.l3_angle_format, ctypes.byref(only_el)).endswith(" valid=e")
    empty = fw.AngleObs()
    assert fw.c_text(lib.l3_angle_format, ctypes.byref(empty)).endswith(" valid=none")


def test_confidence_falls_with_noise_and_is_the_weaker_of_the_two_qualities(lib):
    clean = estimate(lib, synth_snapshot(lib, az_deg=5.0, el_deg=-3.0))[1]
    noisy = estimate(lib, synth_snapshot(lib, az_deg=5.0, el_deg=-3.0, noise=150.0, seed=3))[1]
    assert 0.0 < noisy.confidence < clean.confidence <= 1.0
    expected = min(
        min(1.0, (clean.elevationPeakRatio - 1.0) / (fw_peak_full() - 1.0)),
        clean.azimuthCoherence,
    )
    assert clean.confidence == pytest.approx(expected, abs=1e-5)
    only_el = estimate(lib, synth_snapshot(lib, az_deg=0.0, el_deg=0.0, ntx=2))[1]
    assert only_el.confidence == pytest.approx(
        min(1.0, (only_el.elevationPeakRatio - 1.0) / (fw_peak_full() - 1.0)), abs=1e-5
    ), "without azimuth the peak ratio alone decides"
    assert lib.l3_angle_confidence(1.0, 1.0, 1) == 0.0, "a flat beam is no measurement"
    assert lib.l3_angle_confidence(6.0, 1.0, 1) == pytest.approx(1.0)
    assert lib.l3_angle_confidence(20.0, 0.4, 1) == pytest.approx(0.4), "capped by the coherence"
    assert lib.l3_angle_confidence(20.0, 0.4, 0) == pytest.approx(1.0), (
        "no azimuth: coherence ignored"
    )
    assert lib.l3_angle_confidence(3.5, -1.0, 1) == 0.0


def fw_peak_full() -> float:
    return 6.0  # L3_ANGLE_PEAK_RATIO_FULL


def test_format_prints_the_confidence(lib):
    _, obs = estimate(lib, synth_snapshot(lib, az_deg=10.0, el_deg=-5.0))
    text = fw.c_text(lib.l3_angle_format, ctypes.byref(obs))
    assert " conf=" in text and text.index(" conf=") < text.index(" valid=")
    assert f" conf={obs.confidence:.2f}" in text
