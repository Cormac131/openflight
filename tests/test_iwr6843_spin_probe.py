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
    follow_rois,
    format_report,
    format_rotation_report,
    micro_doppler,
    rotation_spectrum,
    surface_speed_mps,
    track_from_points,
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


def test_compare_paths_matches_frames_and_measures_the_spectral_change():
    from openflight.iwr6843.spin_probe import (
        MicroDoppler,
        PathDifference,
        compare_paths,
        format_comparison,
    )

    axis = np.linspace(-1.0, 1.0, 8)
    spectrum = np.exp(-(axis**2) * 4.0)

    def md(frame, spread, off_bulk, bulk=1.0, spec=spectrum):
        return MicroDoppler(frame, bulk, spread, off_bulk, spec, axis, 0.25)

    a = [md(3, 0.30, 0.10), md(4, 0.32, 0.12), md(5, 0.31, 0.11)]
    b = [md(3, 0.45, 0.30, bulk=1.05), md(5, 0.31, 0.11), md(9, 0.5, 0.5)]
    diffs = compare_paths(a, b)
    assert [d.frame for d in diffs] == [3, 5], "frame 4 has no partner, frame 9 no original"
    first = diffs[0]
    assert isinstance(first, PathDifference)
    assert first.spread_ratio == pytest.approx(1.5) and first.off_bulk_b == 0.30
    assert first.bulk_velocity_b_mps == pytest.approx(1.05)
    assert first.spectrum_correlation == pytest.approx(1.0), "identical shapes correlate fully"
    noisy = compare_paths([md(3, 0.3, 0.1)], [md(3, 0.3, 0.1, spec=np.roll(spectrum, 3))])
    assert noisy[0].spectrum_correlation < 0.9
    text = format_comparison(diffs, label_b="iq8:edma")
    assert "iq16 vs iq8:edma: 2 frames" in text and "median spread ratio" in text
    assert format_comparison([]) == "spin probe A/B: no frames in common"
    zero = compare_paths([md(1, 0.0, 0.0)], [md(1, 0.2, 0.1)])
    assert zero[0].spread_ratio is None


# --- A ball that moves at shot speed --------------------------------------
#
# The synthesis above parks the ball in one bin. A real ball recedes at
# 40-70 m/s radial: 2-3 range bins per frame and about two bins inside one
# 12-loop burst. This synthesis builds the beat signal of every chirp and
# range-FFTs it, so the ball walks across bins exactly as on hardware.

BIN_M = 6.0 / 128
FAST_FRAME_S = 2e-3
FAST_LOOPS = 12
CHIRP_S = 45e-6


def receding_ball_dump(
    *,
    radial_mps: float = 60.0,
    start_m: float = 1.6,
    spin_rpm: float = 0.0,
    modulation: float = 0.0,
    frames: int = 16,
    loops: int = FAST_LOOPS,
    frame_s: float = FAST_FRAME_S,
    noise: float = 0.5,
    last_frame: int | None = None,
    seed: int = 0,
) -> bytes:
    """Range-FFT IQ16 dump of a ball receding at ``radial_mps`` from ``start_m``.

    ``modulation`` is the depth of a once-per-revolution RCS swing at the
    spin rate (a marked or asymmetric ball); 0 is a featureless ball. The
    echo falls off as 1/r^2 in amplitude, as a point target does. After
    ``last_frame`` the ball is gone and only noise is left.
    """
    rng = np.random.default_rng(seed)
    n_tx, n_rx, n = 3, 4, 128
    spin_hz = spin_rpm / 60.0
    fast = np.arange(n)
    cube = np.zeros((frames, loops * n_tx, n_rx, n), dtype=complex)
    for frame in range(frames):
        for chirp in range(loops * n_tx):
            t = frame * frame_s + chirp * CHIRP_S
            r = start_m + radial_mps * t
            if last_frame is not None and frame > last_frame:
                continue
            falloff = (start_m / r) ** 2
            amp = 2000.0 * falloff * (1.0 + modulation * math.cos(2.0 * math.pi * spin_hz * t))
            beat = amp * np.exp(
                1j * (4.0 * math.pi * r / WAVELENGTH_M + 2.0 * math.pi * (r / BIN_M) * fast / n)
            )
            spectrum = np.fft.fft(beat) / n
            for rx in range(n_rx):
                cube[frame, chirp, rx] = spectrum
        cube[frame] += noise * (
            rng.normal(size=cube[frame].shape) + 1j * rng.normal(size=cube[frame].shape)
        )
    return pack_dump(
        cube,
        n_tx=n_tx,
        version=3,
        frame_period_us=int(frame_s * 1e6),
        sample_fmt=SAMPLE_RANGE_FFT_IQ16,
    )


def mid_burst_bin(frame: int, *, radial_mps: float = 60.0, start_m: float = 1.6) -> int:
    t = frame * FAST_FRAME_S + 0.5 * FAST_LOOPS * 3 * CHIRP_S
    return int(round((start_m + radial_mps * t) / BIN_M))


def test_a_ball_walking_across_bins_inside_a_burst_is_not_read_as_spin():
    """Guard: a ball crossing ~2 bins inside a burst is windowed by its dwell in
    the probed bin, which widens its spectrum (0.70 -> 0.83 cells from 30 to
    75 m/s on this synthesis). That bias must stay under the stationary
    threshold. Summing the bins coherently was tried and is worse (~1.7 cells)."""
    raw = receding_ball_dump(radial_mps=60.0, spin_rpm=0.0)
    results = []
    for frame in range(2, 10):
        roi = ball_roi(raw, frames=[frame], center_bin=mid_burst_bin(frame), half_width=3)
        results.extend(micro_doppler(roi))
    spreads = [r.spread_cells for r in results]
    assert max(spreads) < 1.0, f"a spinless ball must stay under one Doppler cell, got {spreads}"
    assert classify_signature(results).label == "stationary"


# --- The ball's track across frames ------------------------------------------


def true_bin(frame: int, *, radial_mps: float = 60.0, start_m: float = 1.6) -> float:
    t = frame * FAST_FRAME_S + 0.5 * FAST_LOOPS * 3 * CHIRP_S
    return (start_m + radial_mps * t) / BIN_M


def true_track(raw: bytes, *, radial_mps: float = 60.0, frames: range = range(16)):
    return track_from_points(raw, [(f, true_bin(f, radial_mps=radial_mps)) for f in frames])


def test_track_from_points_times_each_frame_mid_burst_and_fits_the_radial_speed():
    raw = receding_ball_dump(radial_mps=60.0)
    track = true_track(raw)
    assert track.frames == tuple(range(16))
    half_burst = 0.5 * FAST_LOOPS * 3 * CHIRP_S
    assert track.times_s[0] == pytest.approx(half_burst)
    assert track.times_s[5] == pytest.approx(5 * FAST_FRAME_S + half_burst)
    assert track.radial_mps == pytest.approx(60.0, abs=0.01)
    assert track.span_s == pytest.approx(15 * FAST_FRAME_S)


def test_track_from_points_sorts_by_frame():
    raw = receding_ball_dump(radial_mps=60.0, frames=4)
    track = track_from_points(raw, [(3, 30.0), (1, 26.0), (2, 28.0)])
    assert track.frames == (1, 2, 3) and track.bins == (26.0, 28.0, 30.0)


@pytest.mark.parametrize(
    ("points", "message"),
    [
        ([(0, 20.0)], "two frames"),
        ([], "two frames"),
        ([(0, 20.0), (0, 21.0)], "one point per frame"),
        ([(0, 20.0), (9, 30.0)], "not in this 4-frame dump"),
        ([(-1, 20.0), (1, 30.0)], "not in this 4-frame dump"),
    ],
)
def test_track_from_points_refuses_a_track_it_cannot_time(points, message):
    raw = receding_ball_dump(radial_mps=60.0, frames=4)
    with pytest.raises(ValueError, match=message):
        track_from_points(raw, points)


def test_follow_rois_centre_on_the_ball_and_leave_out_frames_at_the_window_edge():
    raw = receding_ball_dump(radial_mps=60.0, frames=4)
    track = track_from_points(raw, [(0, 1.0), (1, 40.0), (2, 126.4), (3, 50.0)])
    rois = follow_rois(raw, track, half_width=2)
    assert [roi.frames for roi in rois] == [(1,), (3,)], "bins 1 and 126 cannot hold +/-2"
    assert [roi.first_bin for roi in rois] == [38, 48]


# --- Rotation rate from the once-per-revolution modulation -------------------


@pytest.mark.parametrize("rpm", [4500.0, 6000.0, 9000.0])
def test_rotation_spectrum_recovers_a_marked_balls_spin(rpm):
    raw = receding_ball_dump(radial_mps=60.0, spin_rpm=rpm, modulation=0.3)
    track = true_track(raw)
    result = rotation_spectrum(raw, track)
    assert result.detected, format_rotation_report(result)
    assert result.peak_rpm == pytest.approx(rpm, abs=300.0)
    assert result.rotations == pytest.approx(rpm / 60.0 * result.span_s, rel=0.1)
    assert result.resolution_rpm == pytest.approx(60.0 / result.span_s)


def test_a_featureless_ball_shows_no_rotation_line():
    raw = receding_ball_dump(radial_mps=60.0, spin_rpm=6000.0, modulation=0.0)
    result = rotation_spectrum(raw, true_track(raw))
    assert not result.detected, format_rotation_report(result)


def test_the_range_falloff_is_not_read_as_a_slow_rotation():
    """1/r^4 power over the flight is a strong slow trend; detrending must keep it
    out of the lowest frequencies the window can resolve."""
    raw = receding_ball_dump(radial_mps=75.0, spin_rpm=0.0, noise=0.05)
    result = rotation_spectrum(raw, true_track(raw, radial_mps=75.0))
    assert not result.detected, format_rotation_report(result)


def test_driver_spin_in_a_short_window_is_reported_as_below_the_floor():
    """2500 rpm over a 30 ms window is ~1.3 revolutions: under the 1.5-revolution
    floor, so the estimator must say it cannot see it rather than guess."""
    raw = receding_ball_dump(radial_mps=60.0, spin_rpm=2500.0, modulation=0.3)
    result = rotation_spectrum(raw, true_track(raw))
    assert result.floor_rpm > 2500.0
    assert result.floor_rpm == pytest.approx(1.5 * 60.0 / result.span_s)
    assert result.freqs_hz[0] * 60.0 >= result.floor_rpm - 1e-6
    assert result.at_floor and not result.detected, "the leak onto the floor is not a reading"
    report = format_rotation_report(result, reference_rpm=2500.0)
    assert "sits on the floor" in report
    assert "reference 2500 rpm is below the floor" in report


def test_a_band_entirely_below_the_floor_scans_nothing():
    raw = receding_ball_dump(radial_mps=60.0, spin_rpm=2500.0, modulation=0.3)
    track = true_track(raw)
    result = rotation_spectrum(raw, track, f_min_hz=10.0, f_max_hz=30.0)
    assert result.freqs_hz.size == 0 and not result.detected and not result.at_floor
    assert "whole band is below the floor" in format_rotation_report(result)


def test_a_detected_line_is_never_at_the_floor_and_reports_its_reference_error():
    raw = receding_ball_dump(radial_mps=60.0, spin_rpm=6000.0, modulation=0.3)
    result = rotation_spectrum(raw, true_track(raw))
    assert result.detected and not result.at_floor
    report = format_rotation_report(result, reference_rpm=3000.0)
    assert "half the line: " in report, "a stripe at 2x must be checkable against the reference"
    plain = receding_ball_dump(radial_mps=60.0)
    missing = rotation_spectrum(plain, true_track(plain))
    assert "no line to compare" in format_rotation_report(missing, reference_rpm=6000.0)


def test_rotation_report_states_the_window_resolution_and_harmonic_caveat():
    raw = receding_ball_dump(radial_mps=60.0, spin_rpm=6000.0, modulation=0.3)
    result = rotation_spectrum(raw, true_track(raw))
    report = format_rotation_report(result, reference_rpm=6000.0)
    assert report.startswith("spin rotation: ")
    assert "revolutions" in report and "resolution" in report
    assert "twice per revolution" in report, "a stripe reads at 2x: the report must say so"
    assert "reference 6000 rpm" in report
    assert "placeholder" in report


def test_rotation_spectrum_rejects_a_bad_band():
    raw = receding_ball_dump(radial_mps=60.0)
    track = true_track(raw)
    with pytest.raises(ValueError, match="band"):
        rotation_spectrum(raw, track, f_min_hz=200.0, f_max_hz=100.0)


def _load_script():
    spec = importlib.util.spec_from_file_location("spin_probe_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _labelled_dump(tmp_path, raw: bytes, points, *, reviewed: bool = True):
    from openflight.iwr6843.labels import LabelPoint, Labels, dump_sha256, save_labels

    dump = tmp_path / "ball.l3dump"
    dump.write_bytes(raw)
    labels = Labels(
        dump=dump.name,
        dump_sha256=dump_sha256(raw),
        reviewed=reviewed,
        ball=tuple(LabelPoint(f, b) for f, b in points),
    )
    save_labels(dump, labels)
    return dump


def test_script_labels_mode_reports_track_spread_and_rotation(tmp_path, capsys):
    raw = receding_ball_dump(radial_mps=60.0, spin_rpm=6000.0, modulation=0.3)
    dump = _labelled_dump(tmp_path, raw, [(f, true_bin(f)) for f in range(16)])
    out = tmp_path / "follow.npz"
    args = [str(dump), "--labels", "--reference-rpm", "6000", "--save", str(out)]
    assert _load_script().main(args) == 0
    text = capsys.readouterr().out
    assert "ball track: 16 frames" in text and "note:" not in text
    assert "spin probe: " in text and "spin rotation: line at" in text
    assert "reference 6000 rpm: error" in text
    saved = np.load(out)
    assert saved["track_bins"].shape == (16,) and saved["rotation_fraction"].ndim == 1
    assert saved["roi"].shape[0] == 16 and saved["roi_frames"].tolist() == list(range(16))


def test_script_labels_mode_narrows_to_frames_and_flags_unreviewed_labels(tmp_path, capsys):
    raw = receding_ball_dump(radial_mps=60.0, spin_rpm=6000.0, modulation=0.3)
    dump = _labelled_dump(tmp_path, raw, [(f, true_bin(f)) for f in range(16)], reviewed=False)
    assert _load_script().main([str(dump), "--labels", "--frames", "4-11"]) == 0
    text = capsys.readouterr().out
    assert "not marked reviewed" in text and "ball track: 8 frames 4-11" in text


def test_script_labels_mode_reports_frames_at_the_window_edge(tmp_path, capsys):
    raw = receding_ball_dump(radial_mps=60.0, frames=4)
    dump = _labelled_dump(tmp_path, raw, [(0, 1.0), (1, 126.6)])
    out = tmp_path / "edge.npz"
    assert _load_script().main([str(dump), "--labels", "--save", str(out)]) == 0
    text = capsys.readouterr().out
    assert "2 of 2 frames have the ball within 2 bins of their window's edge" in text
    assert "spin probe: " not in text and "spin rotation: " in text
    assert "roi" not in np.load(out).files


def test_script_labels_mode_without_a_label_file_says_so(tmp_path, capsys):
    dump = tmp_path / "ball.l3dump"
    dump.write_bytes(receding_ball_dump(radial_mps=60.0, frames=4))
    assert _load_script().main([str(dump), "--labels"]) == 2
    assert "no label file" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--frames", "0-3", "--bin", "40", "--reference-rpm", "6000"], "needs --labels"),
        (["--labels", "--iq8"], "does not combine with --labels"),
        (["--labels", "--bin", "40"], "not --bin/--range-m"),
        (["--bin", "40"], "needs --frames and one of --bin/--range-m"),
        (["--frames", "0-3"], "needs --frames and one of --bin/--range-m"),
    ],
)
def test_script_refuses_flags_that_do_not_combine(tmp_path, capsys, extra, message):
    dump = tmp_path / "ball.l3dump"
    dump.write_bytes(receding_ball_dump(radial_mps=60.0, frames=4))
    with pytest.raises(SystemExit):
        _load_script().main([str(dump), *extra])
    assert message in capsys.readouterr().err


# --- Recorded swings ---------------------------------------------------------

RECORDINGS = Path(__file__).parent / "radar" / "recordings"


def _labelled_recordings():
    from openflight.iwr6843.labels import load_labels

    out = []
    for path in sorted(RECORDINGS.glob("*.l3dump")):
        labels = load_labels(path)
        if labels is not None and labels.reviewed and len(labels.ball) >= 2:
            out.append((path, labels))
    return out


def test_no_rotation_line_is_claimed_on_the_recorded_plain_ball_swings():
    """The recorded swings have plain balls and no reference spin, so a line
    on any of them could not be checked: at the placeholder threshold the
    estimator must claim none. The track is the reviewed labels; the time in
    view and the radial speed must look like a real flight."""
    recordings = _labelled_recordings()
    if not recordings:
        pytest.skip("no labelled recordings in this checkout")
    claimed = []
    for path, labels in recordings:
        raw = path.read_bytes()
        track = track_from_points(raw, [(p.frame, p.range_bin) for p in labels.ball])
        assert 30.0 <= track.radial_mps <= 70.0, path.name
        result = rotation_spectrum(raw, track)
        assert 0.025 <= result.span_s <= 0.06, path.name
        if result.detected:
            claimed.append((path.name, round(result.peak_rpm), round(result.peak_fraction, 2)))
    assert not claimed, claimed
