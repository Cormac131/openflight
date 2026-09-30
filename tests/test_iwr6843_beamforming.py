"""Bartlett and Capon over the IWR6843 elevation array (beamforming.py)."""

from __future__ import annotations

import ctypes
import math

import numpy as np
import pytest
from iwr6843_synth import synth_shot_dump

from openflight.iwr6843 import beamforming as bf, firmware_host as fw, firmware_replay as fr
from openflight.iwr6843.dump import parse_dump


def plane_waves(angles_deg, powers, *, snapshots=64, noise=0.01, seed=0, n=8):
    """Snapshots of independent sources at the given elevations plus noise."""
    rng = np.random.default_rng(seed)
    out = np.zeros((snapshots, n), dtype=complex)
    for angle, power in zip(angles_deg, powers, strict=True):
        a = bf.steering(np.array([math.radians(angle)]), n)[:, 0]
        s = math.sqrt(power) * np.exp(1j * rng.uniform(0, 2 * np.pi, snapshots))
        out += s[:, None] * a[None, :]
    out += math.sqrt(noise / 2) * (rng.normal(size=out.shape) + 1j * rng.normal(size=out.shape))
    return out


@pytest.fixture(scope="module")
def lib():
    return fr._default_library()  # pylint: disable=protected-access


class TestChirpPhase:
    @pytest.mark.parametrize(
        "lag1, velocity", [(0.5, 0.0), (-2.9, 30.0), (2.0, -25.0), (3.1, 60.0), (-0.2, 9.0)]
    )
    def test_matches_the_firmware(self, lib, lag1, velocity):
        expected = lib.l3_angle_chirp_phase(lag1, 3, velocity, 45e-6)
        assert bf.chirp_phase_rad(lag1, 3, velocity) == pytest.approx(expected, abs=1e-5)

    def test_rejects_no_transmitters(self):
        with pytest.raises(ValueError):
            bf.chirp_phase_rad(0.1, 0, 0.0)


class TestSpectra:
    def test_bartlett_peaks_at_the_source(self):
        spectrum = bf.bartlett_spectrum(plane_waves([12.0], [1.0]))
        assert math.degrees(bf.peak_angle_rad(spectrum)) == pytest.approx(12.0, abs=0.5)

    def test_capon_peaks_at_the_source(self):
        spectrum = bf.capon_spectrum(plane_waves([-7.0], [1.0]))
        assert math.degrees(bf.peak_angle_rad(spectrum)) == pytest.approx(-7.0, abs=0.5)

    def test_capon_resolves_two_sources_bartlett_merges(self):
        snaps = plane_waves([0.0, 9.0], [1.0, 1.0], noise=1e-3)
        grid = bf.GRID_RAD
        for name, spectrum in (
            ("bartlett", bf.bartlett_spectrum(snaps)),
            ("capon", bf.capon_spectrum(snaps, loading=0.001)),
        ):
            between = spectrum[np.argmin(np.abs(grid - math.radians(4.5)))]
            peaks = min(spectrum[np.argmin(np.abs(grid - math.radians(a)))] for a in (0.0, 9.0))
            dip_db = 10 * math.log10(peaks / between)
            if name == "capon":
                assert dip_db > 3.0
            else:
                assert dip_db < 1.0

    def test_capon_rejects_a_strong_interferer_better(self):
        snaps = plane_waves([5.0, -20.0], [1.0, 100.0], noise=1e-3)
        desired, interferer = math.radians(5.0), math.radians(-20.0)
        bartlett = bf.rejection_db(bf.bartlett_weights(desired, 8), desired, interferer)
        capon = bf.rejection_db(bf.capon_weights(snaps, desired, loading=0.01), desired, interferer)
        assert capon > bartlett + 10.0

    def test_capon_weights_are_distortionless(self):
        snaps = plane_waves([5.0, -20.0], [1.0, 100.0])
        w = bf.capon_weights(snaps, math.radians(5.0))
        a = bf.steering(np.array([math.radians(5.0)]), 8)[:, 0]
        assert np.vdot(w, a) == pytest.approx(1.0)

    def test_desired_to_interference_reads_the_spectrum(self):
        grid = np.radians([-10.0, 0.0, 10.0])
        spectrum = np.array([1.0, 0.1, 10.0])
        ratio = bf.desired_to_interference_db(
            spectrum, grid, math.radians(10.0), math.radians(-10.0)
        )
        assert ratio == pytest.approx(10.0)

    def test_capon_inverts_with_fewer_snapshots_than_elements(self):
        spectrum = bf.capon_spectrum(plane_waves([3.0], [1.0], snapshots=2))
        assert np.all(np.isfinite(spectrum)) and np.all(spectrum > 0)

    def test_capon_rejects_negative_loading(self):
        with pytest.raises(ValueError):
            bf.capon_spectrum(plane_waves([0.0], [1.0]), loading=-0.1)

    def test_covariance_is_hermitian(self):
        r = bf.covariance(plane_waves([4.0], [1.0]))
        assert np.allclose(r, r.conj().T)

    def test_covariance_needs_a_matrix(self):
        with pytest.raises(ValueError):
            bf.covariance(np.zeros(8, dtype=complex))

    def test_band_power_falls_back_to_the_nearest_cell(self):
        grid = np.radians([0.0, 10.0, 20.0])
        assert bf.band_power(np.array([1.0, 5.0, 2.0]), grid, math.radians(9.0), 1e-6) == 5.0

    def test_costs(self):
        assert bf.capon_cost(12, 8, 161).inversion == 512
        bartlett = bf.bartlett_cost(12, 8, 161)
        assert bartlett.total == min(12 * 8 * 161, 12 * 64 + 161 * 64)


class TestCompareBeamformers:
    def test_both_steer_at_the_target_outside_the_interferer_lobe(self):
        snaps = plane_waves([8.0, -25.0], [1.0, 10.0], noise=1e-3)
        rows = bf.compare_beamformers(snaps, math.radians(-25.0), loading=0.01)
        assert [r.method for r in rows] == ["bartlett", "capon"]
        for row in rows:
            assert math.degrees(row.desired_rad) == pytest.approx(8.0, abs=1.5)
        by = {r.method: r for r in rows}
        assert by["capon"].rejection_db > by["bartlett"].rejection_db

    def test_a_given_target_is_used(self):
        rows = bf.compare_beamformers(
            plane_waves([0.0], [1.0]), math.radians(-30.0), desired_rad=math.radians(3.0)
        )
        assert all(r.desired_rad == pytest.approx(math.radians(3.0)) for r in rows)

    def test_no_target_when_the_guard_covers_the_grid(self):
        rows = bf.compare_beamformers(plane_waves([0.0], [1.0]), 0.0, guard_rad=math.radians(90.0))
        assert all(r.desired_rad is None and r.rejection_db is None for r in rows)
        assert rows[1].cost.inversion == 512


class TestAgainstTheFirmwareAngle:
    """The element vector and TDM correction are the firmware's: on a
    synthetic shot, the Bartlett peak over the per-loop snapshots matches
    ``l3_angle_estimate`` on the loop-summed snapshot."""

    def test_elevation_and_azimuth_match(self, lib):
        raw = synth_shot_dump(vla_deg=14.0, hla_deg=-8.0, ball_speed_ms=40.0)
        meta, cube = parse_dump(raw)
        n_tx = int(meta["n_tx"])
        frame = 10
        table = fr.bin_observation_table(cube, frame, 0, cube.shape[-1], n_tx)
        local = int(np.argmax(table["peak"][40:])) + 40
        residual = bf.residual_at(cube, frame, local, n_tx)
        lag1 = bf.lag1_phase_rad(residual)
        chirp = bf.chirp_phase_rad(lag1, n_tx, 40.0)
        elements, phasors = bf.elevation_snapshots(residual, chirp)
        elevation = math.degrees(bf.peak_angle_rad(bf.bartlett_spectrum(elements)))
        azimuth, coherence = bf.azimuth_rad(phasors)

        snap = fr.channel_snapshot(
            cube,
            frame,
            local,
            n_tx,
            lag1_phase_rad=lag1,
            radial_velocity_mps=40.0,
            chirp_period_s=45e-6,
        )
        cal = fw.RadarCal()
        lib.l3_cal_identity(ctypes.byref(cal), fw.CAL_MAX_VIRTUAL)
        obs = fw.AngleObs()
        assert lib.l3_angle_estimate(ctypes.byref(cal), ctypes.byref(snap), ctypes.byref(obs))
        assert elevation == pytest.approx(math.degrees(obs.elevationRad), abs=0.6)
        assert math.degrees(azimuth) == pytest.approx(math.degrees(obs.azimuthRad), abs=0.6)
        assert coherence > 0.9

    def test_residual_rejects_a_partial_loop(self):
        cube = np.zeros((1, 7, 4, 8), dtype=complex)
        with pytest.raises(ValueError):
            bf.residual_at(cube, 0, 0, 3)

    def test_elevation_needs_two_transmitters(self):
        with pytest.raises(ValueError):
            bf.elevation_snapshots(np.zeros((4, 1, 4), dtype=complex), 0.0)

    def test_two_tx_has_no_azimuth(self):
        elements, phasors = bf.elevation_snapshots(np.ones((4, 2, 4), dtype=complex), 0.0)
        assert elements.shape == (4, 8) and phasors is None
        assert bf.azimuth_rad(None) == (None, 0.0)

    def test_lag1_of_a_single_loop_is_zero(self):
        assert bf.lag1_phase_rad(np.ones((1, 3, 4), dtype=complex)) == 0.0
