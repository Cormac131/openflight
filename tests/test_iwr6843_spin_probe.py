"""Tests for the EXPERIMENTAL spin probe, openflight.iwr6843.spin_probe.

A ball is synthesised as a bulk Doppler tone plus scatterers on a rotating
surface (micro-Doppler sidebands at up to the surface speed). The probe must
read the bulk velocity back, show a spectral spread that grows with spin, and
label a stationary ball, a slow spinner and a fast spinner in that order.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843.dump import SAMPLE_RANGE_FFT_IQ16, pack_dump
from openflight.iwr6843.spin_probe import (
    WAVELENGTH_M,
    ball_roi,
    bin_of_range,
    classify_signature,
    format_report,
    micro_doppler,
    surface_speed_mps,
)

SCRIPT = Path(__file__).parents[1] / "scripts" / "analysis" / "spin_probe.py"
LOOP_S = 135e-6
FRAME_S = 3e-3
BIN = 52


def spinning_ball_dump(
    *,
    spin_rpm: float,
    bulk_mps: float = 3.0,
    frames: int = 6,
    loops: int = 24,
    scatterers: int = 6,
    seed: int = 0,
) -> bytes:
    """Range-snapshot dump with one ball at BIN: a bulk tone at ``bulk_mps``
    (kept small so it does not alias over 135 us) plus surface scatterers
    whose radial speed swings at the spin rate."""
    rng = np.random.default_rng(seed)
    n_tx, n_rx, bins = 3, 4, 64
    omega = spin_rpm * 2.0 * math.pi / 60.0
    surface = surface_speed_mps(spin_rpm)
    cube = np.zeros((frames, loops * n_tx, n_rx, bins), dtype=complex)
    phases0 = rng.uniform(0, 2 * math.pi, size=scatterers)
    for frame in range(frames):
        for loop in range(loops):
            t = frame * FRAME_S + loop * LOOP_S
            value = 400.0 * np.exp(1j * 4.0 * math.pi * bulk_mps * t / WAVELENGTH_M)
            for k in range(scatterers):
                # A point on the surface: radial offset R sin(omega t + phi).
                radial = (surface / omega) * math.sin(omega * t + phases0[k]) if omega > 0 else 0.0
                value += 150.0 * np.exp(1j * 4.0 * math.pi * (bulk_mps * t + radial) / WAVELENGTH_M)
            for tx in range(n_tx):
                cube[frame, loop * n_tx + tx, :, BIN] = value
            cube[frame, loop * n_tx : (loop + 1) * n_tx, :, :] += 2.0 * (
                rng.normal(size=(n_tx, n_rx, bins)) + 1j * rng.normal(size=(n_tx, n_rx, bins))
            )
    return pack_dump(
        cube,
        n_tx=n_tx,
        version=3,
        frame_period_us=int(FRAME_S * 1e6),
        sample_fmt=SAMPLE_RANGE_FFT_IQ16,
    )


def test_ball_roi_cuts_the_bins_around_the_ball_from_every_requested_frame():
    roi = ball_roi(
        spinning_ball_dump(spin_rpm=0.0), frames=range(1, 4), center_bin=BIN, half_width=2
    )
    assert roi.data.shape == (3, 24, 3, 4, 5)
    assert roi.frames == (1, 2, 3) and roi.first_bin == BIN - 2 and roi.bins == 5
    assert roi.loop_period_s == pytest.approx(LOOP_S)
    assert abs(roi.data[0, 0, 0, 0, 2]) > 100 > abs(roi.data[0, 0, 0, 0, 0])
    with pytest.raises(ValueError, match="outside"):
        ball_roi(spinning_ball_dump(spin_rpm=0.0), frames=[0], center_bin=63, half_width=2)


def test_bulk_velocity_is_read_back_and_a_stationary_ball_has_no_spread():
    results = micro_doppler(
        ball_roi(spinning_ball_dump(spin_rpm=0.0, bulk_mps=3.0), frames=range(6), center_bin=BIN)
    )
    assert len(results) == 6
    for r in results:
        assert r.bulk_velocity_mps == pytest.approx(3.0, abs=0.3)
        assert r.resolution_mps == pytest.approx(0.747, abs=0.01)
        assert r.spread_cells < 1.0, "a single tone spreads under one Doppler cell"
        assert r.off_bulk_fraction < 0.1
    assert classify_signature(results).label == "stationary"


def test_spectral_spread_grows_with_spin_and_orders_the_labels():
    spreads = {}
    labels = {}
    for rpm in (0.0, 1500.0, 6000.0):
        results = micro_doppler(
            ball_roi(spinning_ball_dump(spin_rpm=rpm), frames=range(6), center_bin=BIN)
        )
        signature = classify_signature(results)
        spreads[rpm] = signature.median_spread_mps
        labels[rpm] = signature.label
    assert spreads[0.0] < spreads[1500.0] < spreads[6000.0]
    assert labels[0.0] == "stationary" and labels[6000.0] == "high-spin"
    assert spreads[6000.0] < 1.5 * surface_speed_mps(6000.0), "bounded by the surface speed"


def test_signature_carries_the_placeholder_status_and_report_lists_frames():
    results = micro_doppler(
        ball_roi(spinning_ball_dump(spin_rpm=3000.0), frames=range(3), center_bin=BIN)
    )
    signature = classify_signature(results)
    assert "placeholder" in signature.status
    report = format_report(results, signature)
    assert report.startswith("spin probe: ") and "placeholder thresholds" in report
    assert report.count("\n  frame=") == 3
    with pytest.raises(ValueError):
        classify_signature([])


def test_surface_speed_and_bin_helpers():
    assert surface_speed_mps(3000.0) == pytest.approx(6.7, abs=0.1)
    assert bin_of_range(2.4375) == 52


def test_script_reports_and_saves_the_roi(tmp_path, capsys):
    dump = tmp_path / "ball.l3dump"
    dump.write_bytes(spinning_ball_dump(spin_rpm=3000.0))
    spec = importlib.util.spec_from_file_location("spin_probe_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "roi.npz"
    assert module.main([str(dump), "--frames", "1-4", "--bin", str(BIN), "--save", str(out)]) == 0
    text = capsys.readouterr().out
    assert text.startswith("spin probe: ") and "saved" in text
    saved = np.load(out)
    assert saved["roi"].shape == (4, 24, 3, 4, 5) and saved["spectra"].shape[0] == 4
    assert module.main([str(dump), "--frames", "2", "--range-m", "2.4375"]) == 0
