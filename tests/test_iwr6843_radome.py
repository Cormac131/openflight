"""Radar-front window and hood geometry (radome.py)."""

from __future__ import annotations

import math

import pytest

from openflight.iwr6843 import radome

LOSSLESS = radome.Material(2.7, 0.0)


class TestWavelength:
    def test_free_space_at_62_ghz(self):
        assert radome.wavelength_m(62e9) * 1e3 == pytest.approx(4.835, abs=0.001)

    def test_material_shortens_it(self):
        assert radome.wavelength_m(62e9, 4.0) == pytest.approx(0.5 * radome.wavelength_m(62e9))

    @pytest.mark.parametrize("freq, eps", [(0.0, 1.0), (62e9, 0.0)])
    def test_rejects(self, freq, eps):
        with pytest.raises(ValueError):
            radome.wavelength_m(freq, eps)

    def test_half_waves(self):
        halves = radome.half_wave_thicknesses_mm(4.0, count=3)
        assert halves == pytest.approx([1.209, 2.418, 3.627], abs=0.001)


class TestSlab:
    def test_no_slab_transmits_everything(self):
        assert radome.slab_transmission(0.0, LOSSLESS) == pytest.approx(1.0)

    def test_half_wave_lossless_is_transparent(self):
        half = radome.half_wave_thicknesses_mm(LOSSLESS.eps_r)[0] * 1e-3
        assert radome.slab_transmission(half, LOSSLESS) == pytest.approx(1.0, abs=1e-9)

    def test_quarter_wave_reflects_most(self):
        half = radome.half_wave_thicknesses_mm(LOSSLESS.eps_r)[0] * 1e-3
        quarter = radome.slab_transmission(0.5 * half, LOSSLESS)
        n = math.sqrt(LOSSLESS.eps_r)
        gamma = (1 - n) / (1 + n)
        expected = (1 - gamma**2) ** 2 / (1 + gamma**2) ** 2
        assert quarter == pytest.approx(expected, rel=1e-9)

    def test_loss_lowers_transmission(self):
        half = radome.half_wave_thicknesses_mm(2.7)[0] * 1e-3
        lossy = radome.slab_transmission(half, radome.Material(2.7, 0.02))
        assert lossy < 0.99

    def test_polarisations_agree_at_normal_incidence(self):
        m = radome.MATERIALS["PLA"]
        te = radome.slab_transmission(0.002, m, polarization="TE")
        tm = radome.slab_transmission(0.002, m, polarization="TM")
        assert te == pytest.approx(tm)

    def test_oblique_differs_by_polarisation(self):
        m = radome.MATERIALS["PLA"]
        te = radome.slab_transmission(0.002, m, angle_deg=40, polarization="TE")
        tm = radome.slab_transmission(0.002, m, angle_deg=40, polarization="TM")
        assert te != pytest.approx(tm)

    @pytest.mark.parametrize(
        "kwargs", [{"thickness_m": -1e-3}, {"polarization": "X"}, {"angle_deg": 90.0}]
    )
    def test_rejects(self, kwargs):
        args = {"thickness_m": 1e-3, "material": LOSSLESS, **kwargs}
        thickness = args.pop("thickness_m")
        material = args.pop("material")
        with pytest.raises(ValueError):
            radome.slab_transmission(thickness, material, **args)

    def test_two_way_loss_doubles_the_one_way(self):
        m = radome.MATERIALS["PC"]
        one_way = -10 * math.log10(radome.slab_transmission(0.002, m))
        assert radome.two_way_loss_db(0.002, m) == pytest.approx(2 * one_way)


class TestBestThickness:
    def test_near_the_first_half_wave(self):
        m = radome.MATERIALS["PLA"]
        best, loss = radome.best_thickness_mm(m)
        assert best == pytest.approx(radome.half_wave_thicknesses_mm(m.eps_r)[0], abs=0.15)
        assert loss < radome.worst_two_way_loss_db(0.002, m)

    def test_rejects_a_bad_range(self):
        with pytest.raises(ValueError):
            radome.best_thickness_mm(LOSSLESS, min_mm=3.0, max_mm=2.0)

    def test_standoffs_are_half_free_space_waves(self):
        candidates = radome.standoff_candidates_mm(3)
        assert candidates == pytest.approx([2.418, 4.835, 7.253], abs=0.001)


class TestHood:
    def test_cutoff_and_depth_are_inverse(self):
        depth = radome.hood_depth_for_cutoff_mm(20.0, 25.0)
        assert radome.hood_cutoff_deg(depth, 20.0) == pytest.approx(25.0)

    @pytest.mark.parametrize("depth, half", [(0.0, 20.0), (10.0, 0.0)])
    def test_cutoff_rejects(self, depth, half):
        with pytest.raises(ValueError):
            radome.hood_cutoff_deg(depth, half)

    @pytest.mark.parametrize("half, cutoff", [(20.0, 0.0), (20.0, 90.0), (0.0, 30.0)])
    def test_depth_rejects(self, half, cutoff):
        with pytest.raises(ValueError):
            radome.hood_depth_for_cutoff_mm(half, cutoff)


class TestFrontParams:
    def test_rows_name_every_cad_parameter(self):
        rows = radome.RadarFrontParams(1.52, 4.84, radar_yaw=-5.0).parameter_rows()
        names = [r[0] for r in rows]
        assert names == [
            "radar_window_thickness",
            "radar_window_standoff",
            "radar_window_width",
            "radar_window_height",
            "radar_yaw",
            "radar_pitch",
            "hood_left_depth",
            "hood_right_depth",
            "hood_top_depth",
            "hood_bottom_depth",
        ]
        assert dict((r[0], r[2]) for r in rows)["radar_yaw"] == "deg"

    def test_asymmetric_hood_cutoffs(self):
        params = radome.RadarFrontParams(1.52, 4.84, hood_left_depth=40.0)
        cutoffs = params.hood_cutoffs_deg()
        assert cutoffs["left"] == pytest.approx(math.degrees(math.atan2(20.0, 40.0)))
        assert cutoffs["right"] is None

    @pytest.mark.parametrize(
        "kwargs",
        [{"radar_window_thickness": -1.0}, {"hood_left_depth": -2.0}, {"radar_window_width": 0.0}],
    )
    def test_rejects(self, kwargs):
        args = {"radar_window_thickness": 1.5, "radar_window_standoff": 4.8, **kwargs}
        with pytest.raises(ValueError):
            radome.RadarFrontParams(**args)

    def test_negative_angles_are_allowed(self):
        assert radome.RadarFrontParams(1.5, 4.8, radar_pitch=-10.0).radar_pitch == -10.0
