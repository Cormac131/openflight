"""Angle spectra over the IWR6843 elevation array: Bartlett and Capon (MVDR).

The element vector and TDM correction mirror ``l3_angle_estimate``
(``firmware/iwr6843/l3_angle.c``): the per-loop burst-MTI residual of
``[txA.rx0.., txB.rx0..]`` flipped into physical order, each TX block rotated
back to the loop's first chirp. Unlike the firmware, which sums the loops
coherently into one snapshot, this keeps one snapshot per loop so a Capon
covariance can be formed; Bartlett over the same snapshots is the firmware's
beam (incoherently averaged), so the two are compared on identical input.

Element calibration is not applied here (identity), so absolute angles carry
the board's uncalibrated phase error; spectra compared on the same capture
share it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from openflight.iwr6843.tracking import CHIRP_PERIOD_S

WAVELENGTH_M = 0.00484  # L3_ANGLE_WAVELENGTH_M
ALIAS_SEARCH = 3  # L3_ANGLE_ALIAS_SEARCH
GRID_RAD = np.radians(np.arange(-40.0, 40.001, 0.5))  # L3_ANGLE_GRID_STEPS
DEFAULT_LOADING = 0.1  # diagonal loading, a fraction of the mean element power


def chirp_phase_rad(
    loop_phase_rad: float,
    n_tx: int,
    radial_velocity_mps: float,
    chirp_period_s: float = CHIRP_PERIOD_S,
) -> float:
    """``l3_angle_chirp_phase``: the per-chirp motion phase, the loop phase's
    alias nearest the coarse radial velocity's expectation."""
    if n_tx < 1:
        raise ValueError(f"a loop needs at least one transmitter, got {n_tx}")
    expected = 4.0 * math.pi * radial_velocity_mps * chirp_period_s / WAVELENGTH_M
    candidates = [
        (loop_phase_rad + 2.0 * math.pi * k) / n_tx for k in range(-ALIAS_SEARCH, ALIAS_SEARCH + 1)
    ]
    return min(candidates, key=lambda c: abs(c - expected))


def residual_at(cube: np.ndarray, frame: int, local_bin: int, n_tx: int) -> np.ndarray:
    """The burst-MTI residual at one bin, ``[loops, n_tx, n_rx]``."""
    chirps, n_rx = cube.shape[1], cube.shape[2]
    if chirps % n_tx:
        raise ValueError(f"{chirps} chirps per frame is not a whole number of {n_tx}-TX loops")
    data = cube[frame, :, :, local_bin].reshape(chirps // n_tx, n_tx, n_rx)
    return data - data.mean(axis=0, keepdims=True)


def lag1_phase_rad(residual: np.ndarray) -> float:
    """The per-loop Doppler phase of a residual (``[loops, ...]``), aliased to +/- pi."""
    if residual.shape[0] < 2:
        return 0.0
    return float(np.angle(np.sum(residual[1:] * np.conj(residual[:-1]))))


def elevation_snapshots(
    residual: np.ndarray, chirp_phase: float
) -> tuple[np.ndarray, np.ndarray | None]:
    """Per-loop elevation element vectors ``[loops, 2 n_rx]`` in physical order,
    and the per-loop TX1 azimuth phasors ``[loops, n_rx]`` (None without TX1)."""
    n_tx = residual.shape[1]
    if n_tx < 2:
        raise ValueError("elevation needs two vertical transmitters")
    undo = np.exp(-1j * chirp_phase * np.arange(n_tx))
    corrected = residual * undo[None, :, None]
    tx_a, tx_b = 0, (2 if n_tx == 3 else 1)
    logical = np.concatenate([corrected[:, tx_a, :], corrected[:, tx_b, :]], axis=1)
    elements = logical[:, ::-1]
    if n_tx != 3:
        return elements, None
    reference = 0.5 * (corrected[:, tx_a, :] + corrected[:, tx_b, :])
    return elements, np.conj(reference) * corrected[:, 1, :]


def steering(grid_rad: np.ndarray, n_elements: int) -> np.ndarray:
    """``[n_elements, grid]`` lambda/2 steering vectors, theta positive up."""
    return np.exp(1j * np.pi * np.sin(grid_rad)[None, :] * np.arange(n_elements)[:, None])


def covariance(snapshots: np.ndarray, *, forward_backward: bool = True) -> np.ndarray:
    """Sample covariance of ``[snapshots, elements]``, optionally forward-backward averaged."""
    if snapshots.ndim != 2 or snapshots.shape[0] < 1:
        raise ValueError("covariance needs a [snapshots, elements] array")
    r = snapshots.T @ snapshots.conj() / snapshots.shape[0]
    if forward_backward:
        j = np.eye(r.shape[0])[::-1]
        r = 0.5 * (r + j @ r.conj() @ j)
    return r


def bartlett_spectrum(snapshots: np.ndarray, grid_rad: np.ndarray = GRID_RAD) -> np.ndarray:
    """Conventional beam power per grid angle, averaged over the snapshots."""
    a = steering(grid_rad, snapshots.shape[1])
    beams = snapshots @ a.conj()
    return np.mean(np.abs(beams) ** 2, axis=0) / snapshots.shape[1]


def capon_spectrum(
    snapshots: np.ndarray,
    grid_rad: np.ndarray = GRID_RAD,
    *,
    loading: float = DEFAULT_LOADING,
    forward_backward: bool = True,
) -> np.ndarray:
    """MVDR power ``1 / (a^H R^-1 a)`` per grid angle with diagonal loading
    (``loading`` x the mean element power), so a handful of loops still
    inverts."""
    if loading < 0.0:
        raise ValueError(f"loading must be >= 0, got {loading}")
    r = covariance(snapshots, forward_backward=forward_backward)
    n = r.shape[0]
    power = float(np.real(np.trace(r))) / n
    r_loaded = r + (loading * power + 1e-12) * np.eye(n)
    a = steering(grid_rad, n)
    solved = np.linalg.solve(r_loaded, a)
    denominator = np.real(np.sum(a.conj() * solved, axis=0))
    return 1.0 / np.maximum(denominator, 1e-30)


def peak_angle_rad(spectrum: np.ndarray, grid_rad: np.ndarray = GRID_RAD) -> float:
    """The spectrum's peak with the firmware's parabolic refinement."""
    index = int(np.argmax(spectrum))
    refined = float(index)
    if 0 < index < len(spectrum) - 1:
        left, peak, right = spectrum[index - 1], spectrum[index], spectrum[index + 1]
        denominator = left - 2.0 * peak + right
        if denominator < 0.0:
            refined += 0.5 * (left - right) / denominator
    step = float(grid_rad[1] - grid_rad[0])
    return float(grid_rad[0] + refined * step)


def azimuth_rad(phasors: np.ndarray | None) -> tuple[float | None, float]:
    """``l3_angle_estimate``'s azimuth from TX1 phasors: (azimuth, coherence)."""
    if phasors is None:
        return None, 0.0
    summed = phasors.sum(axis=0)
    magnitude = np.abs(summed)
    unit = np.where(magnitude > 0.0, summed / np.where(magnitude > 0.0, magnitude, 1.0), 0.0)
    mean = unit.sum() / unit.size
    coherence = float(abs(mean))
    if coherence <= 0.0:
        return None, 0.0
    sine = float(np.clip(np.angle(mean) / math.pi, -1.0, 1.0))
    return -math.asin(sine), coherence


def band_power(
    spectrum: np.ndarray, grid_rad: np.ndarray, centre_rad: float, half_width_rad: float
) -> float:
    """The largest spectrum value within ``centre +/- half_width``."""
    mask = np.abs(grid_rad - centre_rad) <= half_width_rad
    if not mask.any():
        mask[int(np.argmin(np.abs(grid_rad - centre_rad)))] = True
    return float(spectrum[mask].max())


def desired_to_interference_db(
    spectrum: np.ndarray,
    grid_rad: np.ndarray,
    desired_rad: float,
    interferer_rad: float,
    *,
    guard_rad: float = math.radians(2.0),
) -> float:
    """``10 log10(P_desired / P_interference)`` read off one spectrum."""
    desired = band_power(spectrum, grid_rad, desired_rad, guard_rad)
    interference = band_power(spectrum, grid_rad, interferer_rad, guard_rad)
    return 10.0 * math.log10(max(desired, 1e-30) / max(interference, 1e-30))


@dataclass(frozen=True)
class BeamformerCost:
    """Complex multiply-accumulates for one spectrum; a DSP budget proxy."""

    covariance: int
    inversion: int
    scan: int

    @property
    def total(self) -> int:
        """All three stages."""
        return self.covariance + self.inversion + self.scan


def bartlett_cost(snapshots: int, elements: int, grid: int) -> BeamformerCost:
    """The cheaper of beaming every snapshot and scanning ``a^H R a``."""
    direct = BeamformerCost(0, 0, snapshots * elements * grid)
    via_covariance = BeamformerCost(snapshots * elements * elements, 0, grid * elements * elements)
    return min(direct, via_covariance, key=lambda c: c.total)


def capon_cost(snapshots: int, elements: int, grid: int) -> BeamformerCost:
    """Covariance, an N^3 solve, and an ``a^H R^-1 a`` scan."""
    return BeamformerCost(snapshots * elements * elements, elements**3, grid * elements * elements)


MAINLOBE_GUARD_RAD = math.asin(2.0 / 8.0)  # the 8-element beam's first null


def _outside_peak(spectrum: np.ndarray, grid_rad: np.ndarray, avoid_rad: float, guard_rad: float):
    mask = np.abs(grid_rad - avoid_rad) > guard_rad
    if not mask.any():
        return None
    candidates = np.where(mask, spectrum, -np.inf)
    return float(grid_rad[int(np.argmax(candidates))])


def bartlett_weights(desired_rad: float, n_elements: int) -> np.ndarray:
    """The conventional beam steered at ``desired_rad``."""
    return steering(np.array([desired_rad]), n_elements)[:, 0] / n_elements


def capon_weights(
    snapshots: np.ndarray, desired_rad: float, *, loading: float = DEFAULT_LOADING
) -> np.ndarray:
    """MVDR weights ``R^-1 a / (a^H R^-1 a)`` (loaded as :func:`capon_spectrum`)."""
    r = covariance(snapshots)
    n = r.shape[0]
    power = float(np.real(np.trace(r))) / n
    a = steering(np.array([desired_rad]), n)[:, 0]
    solved = np.linalg.solve(r + (loading * power + 1e-12) * np.eye(n), a)
    return solved / np.vdot(a, solved)


def rejection_db(weights: np.ndarray, desired_rad: float, interferer_rad: float) -> float:
    """``10 log10(|w^H a_d|^2 / |w^H a_i|^2)``: how much more the beam passes
    from the desired direction than from the interferer's."""
    n = len(weights)
    a_d = steering(np.array([desired_rad]), n)[:, 0]
    a_i = steering(np.array([interferer_rad]), n)[:, 0]
    gain_d = abs(np.vdot(weights, a_d)) ** 2
    gain_i = abs(np.vdot(weights, a_i)) ** 2
    return 10.0 * math.log10(max(gain_d, 1e-30) / max(gain_i, 1e-30))


@dataclass(frozen=True)
class BeamComparison:
    """One beamformer steered at the target: its rejection of the interferer
    and its cost."""

    method: str
    desired_rad: float | None
    rejection_db: float | None
    cost: BeamformerCost


def compare_beamformers(  # pylint: disable=too-many-arguments
    snapshots: np.ndarray,
    interferer_rad: float,
    *,
    desired_rad: float | None = None,
    grid_rad: np.ndarray = GRID_RAD,
    loading: float = DEFAULT_LOADING,
    guard_rad: float = MAINLOBE_GUARD_RAD,
) -> list[BeamComparison]:
    """Bartlett and Capon steered at the same target, each judged by how much
    it rejects the interferer's (the golfer's) direction. The target is
    ``desired_rad``, else the strongest Bartlett direction outside the
    interferer's main lobe (``guard_rad``); None when there is none."""
    n_snap, n_el = snapshots.shape
    if desired_rad is None:
        desired_rad = _outside_peak(
            bartlett_spectrum(snapshots, grid_rad), grid_rad, interferer_rad, guard_rad
        )
    costs = {
        "bartlett": bartlett_cost(n_snap, n_el, len(grid_rad)),
        "capon": capon_cost(n_snap, n_el, len(grid_rad)),
    }
    if desired_rad is None:
        return [BeamComparison(m, None, None, c) for m, c in costs.items()]
    weights = {
        "bartlett": bartlett_weights(desired_rad, n_el),
        "capon": capon_weights(snapshots, desired_rad, loading=loading),
    }
    return [
        BeamComparison(m, desired_rad, rejection_db(weights[m], desired_rad, interferer_rad), cost)
        for m, cost in costs.items()
    ]
