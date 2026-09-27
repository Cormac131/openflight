"""Tests for the IWR6843 observation layer, firmware/iwr6843/l3_observation.c.

Built with the host C compiler (openflight.iwr6843.firmware_host, which
also holds the ctypes mirrors) and driven through ctypes: per-bin residual
observations in, ranked targets with sub-bin range, SNR, coherence, Doppler
and confidence out. Bins are global range-FFT bins.
"""

from __future__ import annotations

import ctypes
import math

import pytest

from openflight.iwr6843.firmware_host import (
    OBS_MAX_TARGETS,
    OBS_WAVELENGTH_M,
    STAT_ENERGY,
    STAT_PEAK,
    BinObs,
    ObsParams as Params,
    TargetObs as Target,
    build_firmware_library,
    host_compiler,
)

MAX_TARGETS = OBS_MAX_TARGETS
WAVELENGTH_M = OBS_WAVELENGTH_M
LOOP_PERIOD_S = 135e-6


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def bins(
    values: dict[int, float],
    first: int = 20,
    count: int = 16,
    noise: float = 100.0,
    coherence: float = 0.9,
    velocity: float = 0.0,
):
    """Region of ``count`` bins from global ``first``; ``values`` are peak powers (energy = 4x)."""
    obs = (BinObs * count)()
    phase = 4.0 * math.pi * velocity * LOOP_PERIOD_S / WAVELENGTH_M
    for i in range(count):
        peak = values.get(first + i, noise * (1.0 + 0.02 * ((i * 7) % 5 - 2)))
        obs[i].peak = peak
        obs[i].energy = 4.0 * peak
        obs[i].loop0 = peak / 3.0
        obs[i].r1Re = coherence * obs[i].energy * math.cos(phase) if first + i in values else 0.0
        obs[i].r1Im = coherence * obs[i].energy * math.sin(phase) if first + i in values else 0.0
    return obs


def extract(
    lib,
    obs,
    *,
    floor: float,
    snr: float = 6.0,
    stat: int = STAT_PEAK,
    first: int = 20,
    frame: int = 7,
    loop_period: float = LOOP_PERIOD_S,
):
    params = Params(stat, snr, loop_period)
    out = (Target * MAX_TARGETS)()
    n = lib.l3_obs_extract(
        ctypes.byref(params), frame, frame * 3000, first, obs, len(obs), floor, out, MAX_TARGETS
    )
    return [out[i] for i in range(n)]


def test_stat_selects_peak_or_energy(lib):
    obs = BinObs(400.0, 100.0, 30.0, 0.0, 0.0)
    assert lib.l3_obs_stat(STAT_PEAK, ctypes.byref(obs)) == 100.0
    assert lib.l3_obs_stat(STAT_ENERGY, ctypes.byref(obs)) == 400.0


def test_median_is_robust_to_a_club_in_a_few_bins(lib):
    obs = bins({25: 9000.0, 26: 7000.0, 27: 5000.0})
    assert lib.l3_obs_median(STAT_PEAK, obs, len(obs)) == pytest.approx(100.0, rel=0.05)
    assert lib.l3_obs_median(STAT_PEAK, obs, 0) == 0.0


def test_floor_seeds_then_smooths_and_never_reaches_zero(lib):
    floor = ctypes.c_float(0.0)
    lib.l3_obs_floor_update(ctypes.byref(floor), STAT_PEAK, bins({}), 16, 3)
    assert floor.value == pytest.approx(100.0, rel=0.05)
    for _ in range(40):
        lib.l3_obs_floor_update(ctypes.byref(floor), STAT_PEAK, bins({}, noise=1000.0), 16, 3)
    assert floor.value == pytest.approx(1000.0, rel=0.05)
    quiet = ctypes.c_float(0.0)
    lib.l3_obs_floor_update(ctypes.byref(quiet), STAT_PEAK, bins({}, noise=0.0), 16, 3)
    assert quiet.value == 1.0


def test_velocity_from_lag_one_phase_and_no_timing_gives_zero(lib):
    phase = 4.0 * math.pi * 2.5 * LOOP_PERIOD_S / WAVELENGTH_M
    assert lib.l3_obs_velocity(math.cos(phase), math.sin(phase), LOOP_PERIOD_S) == pytest.approx(
        2.5, abs=0.02
    )
    assert lib.l3_obs_velocity(1.0, 0.5, 0.0) == 0.0
    assert lib.l3_obs_velocity(0.0, 0.0, LOOP_PERIOD_S) == 0.0


def test_extract_returns_local_maxima_above_threshold_strongest_first(lib):
    obs = bins({25: 3000.0, 26: 2000.0, 30: 5000.0, 34: 500.0})  # 500 is under 6 x 100
    targets = extract(lib, obs, floor=100.0)
    assert [t.peakBin for t in targets] == [30, 25], "26 is on 25's shoulder, 34 is under threshold"
    assert targets[0].snr == pytest.approx(50.0)
    assert targets[0].frame == 7 and targets[0].timestampUs == 21000
    assert targets[0].anglesValid == 0


def test_sub_bin_range_leans_toward_a_strong_neighbour(lib):
    targets = extract(lib, bins({25: 3000.0, 26: 2000.0}), floor=100.0)
    assert 25.3 < targets[0].rangeBin < 25.5
    alone = extract(lib, bins({25: 3000.0}), floor=100.0)
    assert alone[0].rangeBin == pytest.approx(25.0, abs=0.05), "noise neighbours barely pull"


def test_coherence_doppler_and_confidence_are_carried(lib):
    targets = extract(lib, bins({30: 5000.0}, coherence=0.8, velocity=-3.0), floor=100.0)
    t = targets[0]
    assert t.coherence == pytest.approx(0.8, abs=0.01)
    assert t.dopplerAliasMps == pytest.approx(-3.0, abs=0.05)
    assert t.confidence == pytest.approx(0.9, abs=0.02), "far over threshold, coherence 0.8"
    weak = extract(lib, bins({30: 700.0}, coherence=0.0), floor=100.0)[0]
    assert 0.0 < weak.confidence < 0.1, "just over threshold and incoherent"


def test_energy_statistic_thresholds_on_energy(lib):
    obs = bins({30: 1000.0})  # peak 1000, energy 4000; floor given in energy units
    assert len(extract(lib, obs, floor=400.0, stat=STAT_ENERGY)) == 1
    assert len(extract(lib, obs, floor=700.0, stat=STAT_ENERGY)) == 0


def test_extract_caps_at_max_targets_keeping_the_strongest(lib):
    values = {20 + 2 * i: 1000.0 + 100.0 * i for i in range(8)}  # 8 maxima, need count 16
    targets = extract(lib, bins(values, count=16), floor=100.0)
    assert len(targets) == MAX_TARGETS
    small = (Target * 3)()
    params = Params(STAT_PEAK, 6.0, LOOP_PERIOD_S)
    obs = bins(values, count=16)
    n = lib.l3_obs_extract(ctypes.byref(params), 1, 0, 20, obs, 16, 100.0, small, 3)
    assert n == 3 and [t.peakBin for t in small] == [34, 32, 30]


def test_format_target_reads_without_float_printf(lib):
    target = extract(lib, bins({30: 5000.0}, velocity=2.5), floor=100.0)[0]
    buf = ctypes.create_string_buffer(256)
    lib.l3_obs_format_target(ctypes.byref(target), buf, len(buf))
    line = buf.value.decode()
    assert line.startswith("obs frame=7 bin=30 range=30.0") and " snr=50.0 " in line
    assert " v=2.5" in line and line.endswith("angles=none")
