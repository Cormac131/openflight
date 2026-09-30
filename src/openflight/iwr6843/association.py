"""Kinematic club/ball association where power is evidence, not identity.

The firmware's club track picks the most confident target near its
prediction, and after impact the strongest in its window, so a golfer
hotspot brighter than the club wins by amplitude. This host-side tracker
scores every (track, detection) pair on how well the detection continues the
track's motion and direction, with power one small term among six:

    S = w_r S_range + w_v S_velocity + w_a S_accel + w_ang S_angle
        + w_h S_history + w_p S_power

each term in [0, 1]. The score is then weighted by the shot-state field of
view (:func:`fov_weight`) and by ``1 - P_clutter`` (:func:`clutter_probability`),
both soft: nothing is excluded by a box, a fast return agreeing with the club
prediction inside the golfer's region still scores.

The shot state steers where it looks (Phase 5 of the golfer-clutter plan):
broad while waiting, around the club's prediction once acquired, the club's
continuation *and* a new object leaving the impact volume through the impact
window, then the ball corridor. It is an offline harness over recorded dumps
(:func:`track_dump`), judged by :mod:`clutter_bench`; nothing here runs on the
board.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from openflight.iwr6843 import beamforming as bf
from openflight.iwr6843.clutter_map import (
    ClutterConfig,
    ClutterSuppressor,
    RangeAngleClutterMap,
    ShotPhase,
)
from openflight.iwr6843.dump import is_range_snapshot, parse_dump
from openflight.iwr6843.tracking import RANGE_SPAN_M, same_tx_loop_period_s

WAVELENGTH_M = bf.WAVELENGTH_M


@dataclass(frozen=True)
class ScoreWeights:
    """The six evidence weights; normalised so they sum to 1."""

    range: float = 0.30
    velocity: float = 0.25
    acceleration: float = 0.15
    angle: float = 0.15
    history: float = 0.10
    power: float = 0.05

    def __post_init__(self) -> None:
        values = self.as_tuple()
        if any(v < 0.0 for v in values) or sum(values) <= 0.0:
            raise ValueError(f"weights must be >= 0 with a positive sum, got {values}")

    def as_tuple(self) -> tuple[float, ...]:
        """(range, velocity, acceleration, angle, history, power)."""
        return (self.range, self.velocity, self.acceleration, self.angle, self.history, self.power)

    def normalised(self) -> ScoreWeights:
        """The same proportions summing to 1."""
        total = sum(self.as_tuple())
        return ScoreWeights(*(v / total for v in self.as_tuple()))


@dataclass(frozen=True)
class SceneGeometry:
    """What the rig knows before the shot: where the tee is and, optionally,
    the golfer's direction (learned from the range-angle clutter map when
    None). Angles in degrees, elevation positive up, azimuth positive right."""

    tee_range_m: float
    golfer_elevation_deg: float | None = None
    golfer_azimuth_deg: float | None = None
    golfer_sigma_deg: float = 8.0
    ball_elevation_deg: float | None = None  # the corridor's centre; None: any direction
    ball_sigma_deg: float = 15.0


@dataclass(frozen=True)
class TrackerConfig:
    """Gates, noise and weights of the host tracker (all tunable by sweep)."""

    weights: ScoreWeights = field(default_factory=ScoreWeights)
    snr: float = 6.0  # detection threshold over the window's median
    ball_snr: float = 3.0  # after impact, as the board's ball tracker (DEFAULT_BALL_SNR)
    max_accel_mps2: float = 3000.0  # the club decelerates through impact; a ball barely does
    max_detections: int = 8
    gate_bins: float = 3.0  # hard association gate around the prediction
    min_score: float = 0.35
    range_sigma_bins: float = 0.8
    velocity_sigma_mps: float = 6.0
    doppler_sigma_mps: float = 4.5  # the aliased lag-1 readout against the prediction's alias
    # Tighter for P_clutter's club relief: a standing return at the club's
    # range must also move like the club to be excused as the club.
    agreement_doppler_sigma_mps: float = 2.0
    accel_sigma_mps2: float = 4000.0
    angle_sigma_deg: float = 6.0
    history_frames: int = 5  # S_history saturates here
    max_coast_frames: int = 2
    clutter_coast_frames: int = 6  # coasting allowed while predicted inside the hotspot
    fov_floor: float = 0.3  # the least any detection is weighted: soft, never zero
    clutter_weight: float = 0.8  # how much P_clutter scales a score down
    club_min_mps: float = 8.0
    club_max_mps: float = 70.0
    ball_min_mps: float = 15.0
    ball_max_mps: float = 90.0
    ball_origin_bins: float = 6.0  # a new object must leave this close to the tee
    ball_points: int = 3  # consistent points that confirm a ball
    beamformer: str = "bartlett"  # how detection angles are read: "bartlett" or "capon"
    tentative_limit: int = 6

    def __post_init__(self) -> None:
        if self.snr <= 0.0 or self.gate_bins <= 0.0 or self.max_detections < 1:
            raise ValueError("snr, gate_bins and max_detections must be positive")
        if not 0.0 <= self.fov_floor <= 1.0 or not 0.0 <= self.clutter_weight <= 1.0:
            raise ValueError("fov_floor and clutter_weight must be in [0, 1]")
        if self.max_coast_frames < 0 or self.clutter_coast_frames < self.max_coast_frames:
            raise ValueError("clutter_coast_frames must be >= max_coast_frames >= 0")
        if self.beamformer not in ("bartlett", "capon"):
            raise ValueError(f"beamformer must be 'bartlett' or 'capon', got {self.beamformer!r}")


@dataclass(frozen=True)
class Detection:
    """One extracted return: global sub-bin range, power over the window's
    median, the aliased Doppler and, when read, its angles."""

    frame: int
    timestamp_us: int
    range_bin: float
    range_m: float
    power: float
    snr: float
    doppler_mps: float
    lag1_phase_rad: float
    local_bin: int
    elevation_deg: float | None = None
    azimuth_deg: float | None = None


class TrackClass:  # pylint: disable=too-few-public-methods
    """What a track has been confirmed as."""

    TENTATIVE = "tentative"
    CLUB = "club"
    BALL = "ball"


@dataclass
class TrackState:
    """Range, rate and acceleration (m, m/s, m/s^2) at ``timestamp_us``, the
    last direction seen, their variances and the points so far."""

    range_m: float
    velocity_mps: float
    acceleration_mps2: float
    timestamp_us: int
    frame: int
    classification: str = TrackClass.TENTATIVE
    elevation_deg: float | None = None
    azimuth_deg: float | None = None
    range_variance: float = 0.0
    velocity_variance: float = 100.0
    confidence: float = 0.0
    misses: int = 0
    points: list[Detection] = field(default_factory=list)
    # A one-point track has no rate yet: its second point may lie anywhere
    # its class could move to, so it is gated on these implied speeds.
    speed_bounds: tuple[float, float] = (-5.0, 70.0)
    max_accel: float = 3000.0

    def predict(self, timestamp_us: int) -> tuple[float, float]:
        """Constant-acceleration (range, rate) at ``timestamp_us``."""
        dt = (timestamp_us - self.timestamp_us) * 1e-6
        rng = self.range_m + self.velocity_mps * dt + 0.5 * self.acceleration_mps2 * dt * dt
        return rng, self.velocity_mps + self.acceleration_mps2 * dt

    def update(self, det: Detection) -> None:
        """Take a detection: rate and acceleration from the implied motion,
        lightly smoothed; the direction when it was read."""
        dt = (det.timestamp_us - self.timestamp_us) * 1e-6
        if dt > 0.0 and self.points:
            implied = (det.range_m - self.range_m) / dt
            if len(self.points) >= 2:
                accel = (implied - self.velocity_mps) / dt
                accel = 0.5 * self.acceleration_mps2 + 0.5 * accel
                self.acceleration_mps2 = float(np.clip(accel, -self.max_accel, self.max_accel))
            self.velocity_mps = (
                implied if len(self.points) == 1 else (0.3 * self.velocity_mps + 0.7 * implied)
            )
            self.velocity_variance = max(1.0, 0.5 * self.velocity_variance)
        self.range_m, self.timestamp_us, self.frame = det.range_m, det.timestamp_us, det.frame
        if det.elevation_deg is not None:
            self.elevation_deg = det.elevation_deg
        if det.azimuth_deg is not None:
            self.azimuth_deg = det.azimuth_deg
        self.misses = 0
        self.points.append(det)


def fitted_speed(points: Sequence[Detection], count: int = 5) -> float | None:
    """The least-squares range rate over the last ``count`` points (m/s);
    None with fewer than two. A single jump onto a neighbouring return moves
    it far less than the last step's implied rate."""
    recent = list(points)[-count:]
    if len(recent) < 2:
        return None
    t = np.array([p.timestamp_us for p in recent], dtype=float) * 1e-6
    r = np.array([p.range_m for p in recent], dtype=float)
    if np.ptp(t) <= 0.0:
        return None
    return float(np.polyfit(t - t[0], r, 1)[0])


def alias_velocity(velocity_mps: float, span_mps: float) -> float:
    """``velocity`` folded into the lag-1 readout's +/- span / 2."""
    return (velocity_mps + 0.5 * span_mps) % span_mps - 0.5 * span_mps


def _gauss(error: float, sigma: float) -> float:
    return math.exp(-0.5 * (error / sigma) ** 2) if sigma > 0.0 else 0.0


def evidence(  # pylint: disable=too-many-arguments
    track: TrackState,
    det: Detection,
    config: TrackerConfig,
    *,
    bin_m: float,
    velocity_span_mps: float,
    frame_peak_power: float,
) -> dict[str, float] | None:
    """The six terms for one pair, each in [0, 1]; None outside the hard gate.
    A one-point track is gated on its class's speed bounds instead, its range
    and rate terms neutral, since it has no prediction yet."""
    dt = (det.timestamp_us - track.timestamp_us) * 1e-6
    if dt <= 0.0:
        return None
    implied = (det.range_m - track.range_m) / dt
    predicted_m, predicted_v = track.predict(det.timestamp_us)
    if len(track.points) < 2:
        lo, hi = track.speed_bounds
        if not lo <= implied <= hi:
            return None
        predicted_m, predicted_v = det.range_m, implied
    range_error_bins = (det.range_m - predicted_m) / bin_m
    if abs(range_error_bins) > config.gate_bins:
        return None
    sigma_bins = math.hypot(config.range_sigma_bins, math.sqrt(track.range_variance) / bin_m)
    rate = _gauss(implied - predicted_v, config.velocity_sigma_mps)
    doppler_error = alias_velocity(det.doppler_mps - predicted_v, velocity_span_mps)
    doppler = _gauss(doppler_error, config.doppler_sigma_mps)
    accel = (implied - track.velocity_mps) / dt if len(track.points) >= 2 else 0.0
    angles = []
    if det.elevation_deg is not None and track.elevation_deg is not None:
        angles.append(_gauss(det.elevation_deg - track.elevation_deg, config.angle_sigma_deg))
    if det.azimuth_deg is not None and track.azimuth_deg is not None:
        angles.append(_gauss(det.azimuth_deg - track.azimuth_deg, config.angle_sigma_deg))
    power = 0.0
    if frame_peak_power > 1.0 and det.snr > 1.0:
        power = min(1.0, math.log(det.snr) / math.log(frame_peak_power))
    return {
        "range": _gauss(range_error_bins, sigma_bins),
        "velocity": 0.5 * (rate + doppler),
        "acceleration": _gauss(accel, config.accel_sigma_mps2),
        "angle": sum(angles) / len(angles) if angles else 0.5,
        "history": min(1.0, len(track.points) / config.history_frames),
        "power": power,
    }


def combine(terms: dict[str, float], weights: ScoreWeights) -> float:
    """The weighted evidence score in [0, 1]."""
    w = weights.normalised()
    return (
        w.range * terms["range"]
        + w.velocity * terms["velocity"]
        + w.acceleration * terms["acceleration"]
        + w.angle * terms["angle"]
        + w.history * terms["history"]
        + w.power * terms["power"]
    )


@dataclass(frozen=True)
class ClutterEvidence:
    """What :func:`clutter_probability` weighs, each in [0, 1]."""

    region: float  # inside the golfer's learned (or configured) direction and range
    background: float  # its power is close to the learned background there
    slow: float  # low radial speed
    persistent: float  # a return has sat at this range on recent frames
    club_agreement: float  # agrees with the club track's prediction


CLUTTER_BIAS = -3.0
CLUTTER_WEIGHTS = (2.0, 2.0, 1.5, 2.0)  # region, background, slow, persistent
CLUB_RELIEF = 5.0


def clutter_probability(ev: ClutterEvidence) -> float:
    """``P_clutter``: a logistic of the golfer-like evidence less the club's
    agreement, so a fast return on the club's prediction inside the golfer's
    region is still not clutter."""
    features = (ev.region, ev.background, ev.slow, ev.persistent)
    logit = CLUTTER_BIAS + sum(w * f for w, f in zip(CLUTTER_WEIGHTS, features, strict=True))
    logit -= CLUB_RELIEF * ev.club_agreement
    return 1.0 / (1.0 + math.exp(-logit))


def golfer_membership(det: Detection, geometry: SceneGeometry) -> float:
    """How far inside the configured golfer direction a detection lies (0..1);
    0 when the direction or the detection's angles are unknown."""
    terms = []
    if geometry.golfer_elevation_deg is not None and det.elevation_deg is not None:
        terms.append(
            _gauss(det.elevation_deg - geometry.golfer_elevation_deg, geometry.golfer_sigma_deg)
        )
    if geometry.golfer_azimuth_deg is not None and det.azimuth_deg is not None:
        terms.append(
            _gauss(det.azimuth_deg - geometry.golfer_azimuth_deg, geometry.golfer_sigma_deg)
        )
    return min(terms) if terms else 0.0


def ball_reachable(
    det: Detection, geometry: SceneGeometry, config: TrackerConfig, *, bin_m: float, impact_us: int
) -> bool:
    """Whether a ball that left the tee at ``impact_us`` could be at the
    detection's range: from ``ball_origin_bins`` short of the tee to where
    the fastest ball would have got since."""
    dt = max((det.timestamp_us - impact_us) * 1e-6, 0.0)
    tee_bins = geometry.tee_range_m / bin_m
    lo = tee_bins - config.ball_origin_bins
    hi = tee_bins + config.ball_origin_bins + config.ball_max_mps * dt / bin_m
    return lo <= det.range_bin <= hi


def fov_weight(  # pylint: disable=too-many-arguments
    det: Detection,
    phase: ShotPhase,
    geometry: SceneGeometry,
    config: TrackerConfig,
    *,
    bin_m: float,
    club: TrackState | None = None,
    golfer: float = 0.0,
    impact_us: int | None = None,
) -> float:
    """The shot-state field of view, soft: between ``fov_floor`` and 1.

    Waiting: everywhere, the golfer's region down-weighted. Club acquired:
    around the club's predicted range. Impact window and after: the club's
    continuation, or the ball corridor -- anywhere a ball leaving the tee at
    impact could be, along the launch direction when one is configured."""
    floor = config.fov_floor
    if phase in (ShotPhase.IDLE, ShotPhase.BACKGROUND_LEARNING):
        return max(floor, 1.0 - (1.0 - floor) * golfer)
    near_club = 0.0
    if club is not None:
        predicted_m, _ = club.predict(det.timestamp_us)
        near_club = _gauss((det.range_m - predicted_m) / bin_m, config.gate_bins)
    if phase in (ShotPhase.CLUB_APPROACH_DETECTED, ShotPhase.PRE_IMPACT_TRACKING):
        weight = near_club if club is not None else 1.0
    else:
        corridor = 0.0
        if impact_us is not None and ball_reachable(
            det, geometry, config, bin_m=bin_m, impact_us=impact_us
        ):
            corridor = 1.0
            if geometry.ball_elevation_deg is not None and det.elevation_deg is not None:
                corridor = _gauss(
                    det.elevation_deg - geometry.ball_elevation_deg, geometry.ball_sigma_deg
                )
        weight = max(near_club, corridor * (1.0 - golfer))
    return floor + (1.0 - floor) * weight


def extract_detections(  # pylint: disable=too-many-arguments
    table: np.ndarray,
    window_start: int,
    frame: int,
    timestamp_us: int,
    *,
    stat: str,
    snr: float,
    loop_period_s: float,
    bin_m: float,
    max_detections: int,
) -> list[Detection]:
    """Local maxima of the statistic at ``snr`` x the window's median, the
    strongest ``max_detections``, with parabolic sub-bin range and the
    aliased Doppler of ``l3_obs_velocity``."""
    values = np.asarray(table[stat], dtype=float)
    if values.size < 3:
        return []
    floor = max(float(np.median(values)), 1e-12)
    found = []
    for i in range(1, values.size - 1):
        v = values[i]
        if v < snr * floor or v < values[i - 1] or v < values[i + 1]:
            continue
        left, right = values[i - 1], values[i + 1]
        denominator = left - 2.0 * v + right
        offset = 0.5 * (left - right) / denominator if denominator < 0.0 else 0.0
        range_bin = window_start + i + float(np.clip(offset, -0.5, 0.5))
        phase = math.atan2(float(table["r1Im"][i]), float(table["r1Re"][i]))
        doppler = phase * WAVELENGTH_M / (4.0 * math.pi * loop_period_s)
        found.append(
            Detection(
                frame, timestamp_us, range_bin, range_bin * bin_m, v, v / floor, doppler, phase, i
            )
        )
    found.sort(key=lambda d: d.power, reverse=True)
    return found[:max_detections]


def read_angles(
    cube: np.ndarray,
    det: Detection,
    n_tx: int,
    radial_velocity_mps: float,
    beamformer: str = "bartlett",
) -> Detection:
    """The detection with its elevation (the ``beamformer`` spectrum's peak)
    and TX1 azimuth, the TDM alias resolved against ``radial_velocity_mps``."""
    residual = bf.residual_at(cube, det.frame, det.local_bin, n_tx)
    chirp = bf.chirp_phase_rad(det.lag1_phase_rad, n_tx, radial_velocity_mps)
    elements, phasors = bf.elevation_snapshots(residual, chirp)
    spectrum = (
        bf.capon_spectrum(elements) if beamformer == "capon" else bf.bartlett_spectrum(elements)
    )
    elevation = math.degrees(bf.peak_angle_rad(spectrum))
    azimuth, _ = bf.azimuth_rad(phasors)
    return replace(
        det,
        elevation_deg=elevation,
        azimuth_deg=None if azimuth is None else math.degrees(azimuth),
    )


@dataclass
class HostTrackResult:
    """One dump through the host tracker."""

    club_points: list[Detection]
    ball_points: list[Detection]
    impact_frame: int | None
    phases: list[str]
    clutter_probabilities: list[tuple[int, float, float]]  # (frame, range bin, P_clutter)
    golfer_direction: tuple[float, float | None] | None  # learned (elevation, azimuth), degrees


def _persistence(history: Sequence[Sequence[float]], range_bin: float) -> float:
    if not history:
        return 0.0
    hits = sum(1 for bins in history if any(abs(b - range_bin) <= 1.0 for b in bins))
    return hits / len(history)


class HostTracker:  # pylint: disable=too-many-instance-attributes
    """The host club/ball tracker over one capture's frames (see module doc).

    Every track starts tentative from one unclaimed, unlikely-clutter
    detection and is confirmed by its motion: a club by ``approach_points``
    rising points at a club's speed short of the tee; a ball by two points
    faster than the club that a ball leaving the tee at impact could reach;
    after impact a lost club by three departing points slower than the ball.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        config: TrackerConfig,
        geometry: SceneGeometry,
        *,
        bin_m: float,
        velocity_span_mps: float,
        range_angle: RangeAngleClutterMap | None = None,
        hotspot_bin: float | None = None,
        external_impact: bool = False,
    ) -> None:
        self.config = config
        self.geometry = geometry
        self.bin_m = bin_m
        self.span = velocity_span_mps
        self.range_angle = range_angle
        self.hotspot_bin = hotspot_bin
        self.external_impact = external_impact
        self.phase = ShotPhase.BACKGROUND_LEARNING
        self.club: TrackState | None = None
        self.ball: TrackState | None = None
        self.club_approach_mps = 0.0
        self.tentative: list[TrackState] = []
        self.club_tentative: list[TrackState] = []
        self.impact_frame: int | None = None
        self.impact_us: int | None = None
        self.recent_bins: list[list[float]] = []
        self.club_points: list[Detection] = []
        self.ball_points: list[Detection] = []
        self.probabilities: list[tuple[int, float, float]] = []

    @property
    def post_impact(self) -> bool:
        """Whether impact has been declared."""
        return self.impact_us is not None

    def velocity_hint(self, det: Detection) -> float:
        """The radial speed that resolves a detection's TDM alias: the
        prediction of an active track within the gate, else its own aliased
        Doppler (right for anything slower than the readout's span)."""
        for track in (self.ball, self.club):
            if track is None:
                continue
            predicted_m, predicted_v = track.predict(det.timestamp_us)
            if abs(det.range_m - predicted_m) / self.bin_m <= self.config.gate_bins:
                return predicted_v
        return det.doppler_mps

    def _clutter(self, det: Detection, background_ratio: float) -> float:
        region = golfer_membership(det, self.geometry)
        if self.range_angle is not None and det.elevation_deg is not None:
            learned = self.range_angle.angular_fraction(
                int(round(det.range_bin)), math.radians(det.elevation_deg)
            )
            region = max(region, learned)
        if self.hotspot_bin is not None:
            region *= _gauss(det.range_bin - self.hotspot_bin, 3.0)
        agreement = 0.0
        if self.club is not None:
            predicted_m, predicted_v = self.club.predict(det.timestamp_us)
            agreement = _gauss((det.range_m - predicted_m) / self.bin_m, 1.5) * _gauss(
                alias_velocity(det.doppler_mps - predicted_v, self.span),
                self.config.agreement_doppler_sigma_mps,
            )
        ev = ClutterEvidence(
            region=region,
            background=min(1.0, max(0.0, background_ratio)),
            slow=1.0 - min(abs(det.doppler_mps) / 4.0, 1.0),
            persistent=_persistence(self.recent_bins[-6:], det.range_bin),
            club_agreement=agreement,
        )
        return clutter_probability(ev)

    def _score(self, track: TrackState, det: Detection, p_clutter: float, peak: float) -> float:
        terms = evidence(
            track,
            det,
            self.config,
            bin_m=self.bin_m,
            velocity_span_mps=self.span,
            frame_peak_power=peak,
        )
        if terms is None:
            return 0.0
        fov = fov_weight(
            det,
            self.phase,
            self.geometry,
            self.config,
            bin_m=self.bin_m,
            club=self.club,
            golfer=golfer_membership(det, self.geometry),
            impact_us=self.impact_us,
        )
        clutter = 1.0 - self.config.clutter_weight * p_clutter
        return combine(terms, self.config.weights) * fov * clutter

    def _coast_limit(self, track: TrackState, timestamp_us: int) -> int:
        if self.hotspot_bin is None:
            return self.config.max_coast_frames
        predicted_m, _ = track.predict(timestamp_us)
        inside = abs(predicted_m / self.bin_m - self.hotspot_bin) <= 3.0
        return self.config.clutter_coast_frames if inside else self.config.max_coast_frames

    def _best(
        self, track: TrackState, detections, probs, free: list[int], peak: float
    ) -> int | None:
        best, best_score = None, self.config.min_score
        for i in free:
            score = self._score(track, detections[i], probs[i], peak)
            if score > best_score:
                best, best_score = i, score
        if best is not None:
            track.confidence = best_score
        return best

    def _advance(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, track: TrackState, detections, probs, free, peak, accept
    ) -> bool:
        """Associate the track's best detection when ``accept`` allows it."""
        index = self._best(track, detections, probs, free, peak)
        if index is None or not accept(track, detections[index]):
            track.misses += 1
            return False
        track.update(detections[index])
        free.remove(index)
        return True

    def _grow(self, tentatives, detections, probs, free, peak) -> list[TrackState]:
        survivors = []
        for track in tentatives:
            self._advance(track, detections, probs, free, peak, lambda t, d: True)
            if track.misses <= self.config.max_coast_frames:
                survivors.append(track)
        return survivors

    def _seed(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, tentatives, detections, probs, free, bounds, where
    ) -> None:
        for i in sorted(free, key=lambda j: probs[j]):
            if len(tentatives) >= self.config.tentative_limit:
                return
            d = detections[i]
            if probs[i] < 0.5 and where(d):
                tentatives.append(
                    TrackState(
                        d.range_m,
                        0.0,
                        0.0,
                        d.timestamp_us,
                        d.frame,
                        points=[d],
                        speed_bounds=bounds,
                        max_accel=self.config.max_accel_mps2,
                    )
                )

    def step(
        self,
        frame: int,
        timestamp_us: int,
        detections: list[Detection],
        background_ratios: list[float],
        *,
        impact_now: bool = False,
    ) -> None:
        """One frame: classify clutter, associate, start and confirm tracks."""
        peak = max((d.snr for d in detections), default=1.0)
        probs = [self._clutter(d, r) for d, r in zip(detections, background_ratios, strict=True)]
        self.probabilities.extend(
            (frame, d.range_bin, p) for d, p in zip(detections, probs, strict=True)
        )
        free = list(range(len(detections)))
        if impact_now and not self.post_impact:
            self._declare_impact(frame, timestamp_us)
        if self.post_impact:
            self._ball_step(detections, probs, free, peak)
            self._club_out_step(detections, probs, free, peak, timestamp_us)
        else:
            self._pre_impact_step(detections, probs, free, peak, timestamp_us)
            if (
                self.club is not None
                and not self.external_impact
                and self.club.predict(timestamp_us)[0] >= self.geometry.tee_range_m
            ):
                self._declare_impact(frame, timestamp_us)
        self.recent_bins.append([d.range_bin for d in detections])
        if self.phase is ShotPhase.IMPACT_WINDOW and self.ball is not None:
            self.phase = ShotPhase.POST_IMPACT_TRACKING

    def _declare_impact(self, frame: int, timestamp_us: int) -> None:
        self.impact_frame, self.impact_us = frame, timestamp_us
        self.phase = ShotPhase.IMPACT_WINDOW
        self.tentative = []
        if self.club is not None:
            fitted = fitted_speed(self.club.points) or 0.0
            self.club_approach_mps = max(fitted, self.config.club_min_mps)

    def _club_accept_pre(self, club: TrackState, det: Detection) -> bool:
        dt = (det.timestamp_us - club.timestamp_us) * 1e-6
        speed = (det.range_m - club.range_m) / dt if dt > 0.0 else math.inf
        return -2.0 <= speed <= self.config.club_max_mps

    def _club_accept_post(self, club: TrackState, det: Detection) -> bool:
        """Only a departing return slower than the ball (and than 1.1 x the
        approach) continues the club after impact."""
        dt = (det.timestamp_us - club.timestamp_us) * 1e-6
        if dt <= 0.0:
            return False
        speed = (det.range_m - club.range_m) / dt
        slower = self.ball is None or speed < self.ball.velocity_mps
        return -2.0 <= speed <= 1.1 * self.club_approach_mps and slower

    def _pre_impact_step(self, detections, probs, free, peak, timestamp_us) -> None:
        if self.club is not None:
            if self._advance(self.club, detections, probs, free, peak, self._club_accept_pre):
                self.club_points.append(self.club.points[-1])
            elif self.club.misses > self._coast_limit(self.club, timestamp_us):
                self.club = None
                self.phase = ShotPhase.BACKGROUND_LEARNING
            return
        self.tentative = self._grow(self.tentative, detections, probs, free, peak)
        for track in self.tentative:
            if self._club_plausible(track):
                track.classification = TrackClass.CLUB
                self.club = track
                self.club_points.extend(track.points)
                self.tentative = []
                self.phase = ShotPhase.PRE_IMPACT_TRACKING
                return
        self._seed(
            self.tentative,
            detections,
            probs,
            free,
            (-5.0, self.config.club_max_mps),
            lambda d: d.range_m <= self.geometry.tee_range_m + self.bin_m,
        )

    def _club_plausible(self, track: TrackState) -> bool:
        recent = track.points[-3:]
        if len(recent) < 3:
            return False
        for older, newer in zip(recent, recent[1:], strict=False):
            dt = (newer.timestamp_us - older.timestamp_us) * 1e-6
            if dt <= 0.0:
                return False
            speed = (newer.range_m - older.range_m) / dt
            if not self.config.club_min_mps <= speed <= self.config.club_max_mps:
                return False
        return recent[-1].range_m <= self.geometry.tee_range_m + self.bin_m

    def _reachable(self, det: Detection) -> bool:
        return self.impact_us is not None and ball_reachable(
            det, self.geometry, self.config, bin_m=self.bin_m, impact_us=self.impact_us
        )

    def _ball_step(self, detections, probs, free, peak) -> None:
        if self.ball is not None:
            if self._advance(self.ball, detections, probs, free, peak, self._ball_accept):
                self.ball_points.append(self.ball.points[-1])
            return
        self.tentative = self._grow(self.tentative, detections, probs, free, peak)
        plausible = [t for t in self.tentative if self._ball_plausible(t)]
        if plausible:
            best = max(plausible, key=lambda t: (len(t.points), fitted_speed(t.points) or 0.0))
            best.classification = TrackClass.BALL
            self.ball = best
            self.ball_points.extend(best.points)
            self.tentative = []
            return
        bounds = (self.config.ball_min_mps, self.config.ball_max_mps)
        self._seed(self.tentative, detections, probs, free, bounds, self._reachable)

    def _ball_accept(self, ball: TrackState, det: Detection) -> bool:
        dt = (det.timestamp_us - ball.timestamp_us) * 1e-6
        speed = (det.range_m - ball.range_m) / dt if dt > 0.0 else -1.0
        return 0.5 * self.config.ball_min_mps <= speed <= self.config.ball_max_mps

    def _ball_plausible(self, track: TrackState) -> bool:
        """``ball_points`` points, each step at a ball's speed and within
        30 % of the fitted rate, faster than the club's approach, the first
        where a ball leaving the tee at impact could be."""
        recent = track.points[-self.config.ball_points :]
        if len(recent) < self.config.ball_points or self.impact_us is None:
            return False
        speed = fitted_speed(recent)
        if speed is None or not self.config.ball_min_mps <= speed <= self.config.ball_max_mps:
            return False
        for older, newer in zip(recent, recent[1:], strict=False):
            dt = (newer.timestamp_us - older.timestamp_us) * 1e-6
            if dt <= 0.0 or abs((newer.range_m - older.range_m) / dt - speed) > 0.3 * speed:
                return False
        return speed > self.club_approach_mps and self._reachable(recent[0])

    def _club_out_step(self, detections, probs, free, peak, timestamp_us) -> None:
        """The club after impact: continued while it holds, else re-acquired
        from departing returns slower than the ball near the tee."""
        if self.club is not None and self.club.misses <= self._coast_limit(self.club, timestamp_us):
            if self._advance(self.club, detections, probs, free, peak, self._club_accept_post):
                self.club_points.append(self.club.points[-1])
            return
        self.club_tentative = self._grow(self.club_tentative, detections, probs, free, peak)
        for track in self.club_tentative:
            if self._club_out_plausible(track):
                track.classification = TrackClass.CLUB
                self.club = track
                self.club_points.extend(track.points)
                self.club_tentative = []
                return
        bounds = (1.0, 1.1 * max(self.club_approach_mps, self.config.club_min_mps))
        self._seed(self.club_tentative, detections, probs, free, bounds, self._reachable)

    def _club_out_plausible(self, track: TrackState) -> bool:
        recent = track.points[-3:]
        if len(recent) < 3:
            return False
        for older, newer in zip(recent, recent[1:], strict=False):
            if not self._club_accept_post(
                TrackState(older.range_m, 0.0, 0.0, older.timestamp_us, older.frame), newer
            ):
                return False
            if newer.range_m <= older.range_m:
                return False
        return True


def track_dump(  # pylint: disable=too-many-locals,too-many-arguments
    raw: bytes,
    geometry: SceneGeometry,
    *,
    config: TrackerConfig | None = None,
    clutter: ClutterConfig | None = None,
    stat: str = "peak",
    impact_frame: int | None = None,
    fft_size: int = 128,
) -> HostTrackResult:
    """Run one range-snapshot dump through the clutter map and the host
    tracker. ``impact_frame`` (the sound trigger's freeze) declares impact
    there; without it impact is when the club's prediction reaches the tee."""
    from openflight.iwr6843.firmware_replay import (  # pylint: disable=import-outside-toplevel
        bin_observation_table,
        frame_timestamps_us,
        frame_window,
    )

    config = config or TrackerConfig()
    meta, cube = parse_dump(raw)
    if not is_range_snapshot(meta):
        raise ValueError("the host tracker needs a range-FFT snapshot dump")
    cube = cube.copy()
    n_tx = int(meta["n_tx"])
    loop_period_s = same_tx_loop_period_s(n_tx)
    bin_m = RANGE_SPAN_M / fft_size
    span = 2.0 * WAVELENGTH_M / (4.0 * loop_period_s)
    timestamps = frame_timestamps_us(meta)
    clutter = clutter or ClutterConfig(beta=0.0)
    suppressor = ClutterSuppressor(
        clutter, lambda c, f, n, t: bin_observation_table(c, f, 0, n, t)[stat], fft_size
    )
    range_angle = RangeAngleClutterMap(clutter, bf.GRID_RAD, fft_size)
    tracker = HostTracker(
        config,
        geometry,
        bin_m=bin_m,
        velocity_span_mps=span,
        range_angle=range_angle,
        external_impact=impact_frame is not None,
    )
    for frame in range(int(meta["n_frames"])):
        start, count = frame_window(meta, frame)
        impact = tracker.impact_frame is not None or (
            impact_frame is not None and frame >= impact_frame
        )
        learning_before = suppressor.machine.learning
        suppressor.frame(
            cube,
            frame,
            start,
            count,
            n_tx,
            club_active=tracker.club is not None,
            impact=impact,
            shot_done=False,
        )
        if count <= 0:
            continue
        table = bin_observation_table(cube, frame, 0, count, n_tx)
        detections = extract_detections(
            table,
            start,
            frame,
            timestamps[frame],
            stat=stat,
            snr=config.ball_snr if tracker.post_impact else config.snr,
            loop_period_s=loop_period_s,
            bin_m=bin_m,
            max_detections=config.max_detections,
        )
        detections = [
            read_angles(cube, d, n_tx, tracker.velocity_hint(d), config.beamformer)
            for d in detections
        ]
        background = suppressor.map.background(start, count)
        ratios = [
            float(background[d.local_bin] / d.power) if d.power > 0.0 else 0.0 for d in detections
        ]
        if learning_before and suppressor.machine.learning:
            _learn_angles(range_angle, cube, frame, start, count, n_tx)
        tracker.step(
            frame,
            timestamps[frame],
            detections,
            ratios,
            impact_now=impact_frame is not None and frame == impact_frame,
        )
        if (
            tracker.hotspot_bin is None
            and range_angle.estimator.updates.max() >= clutter.min_updates
        ):
            tracker.hotspot_bin = _hotspot_bin(suppressor, geometry, bin_m)
    direction = None
    if tracker.hotspot_bin is not None:
        lo = max(int(tracker.hotspot_bin) - 2, 0)
        found = range_angle.dominant_direction(lo, 5)
        if found is not None:
            elevation, azimuth = found
            direction = (
                math.degrees(elevation),
                None if azimuth is None else math.degrees(azimuth),
            )
    return HostTrackResult(
        tracker.club_points,
        tracker.ball_points,
        tracker.impact_frame,
        list(suppressor.phases),
        tracker.probabilities,
        direction,
    )


def _learn_angles(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    range_angle: RangeAngleClutterMap,
    cube: np.ndarray,
    frame: int,
    start: int,
    count: int,
    n_tx: int,
) -> None:
    spectra = np.zeros((count, len(range_angle.grid_rad)))
    phasors = np.zeros(count, dtype=complex)
    for local in range(count):
        residual = bf.residual_at(cube, frame, local, n_tx)
        chirp = bf.chirp_phase_rad(bf.lag1_phase_rad(residual), n_tx, 0.0)
        elements, tx1 = bf.elevation_snapshots(residual, chirp)
        spectra[local] = bf.bartlett_spectrum(elements, range_angle.grid_rad)
        if tx1 is not None:
            phasors[local] = complex(tx1.sum())
    range_angle.update(start, spectra, phasors)


def _hotspot_bin(
    suppressor: ClutterSuppressor, geometry: SceneGeometry, bin_m: float
) -> float | None:
    tee = int(round(geometry.tee_range_m / bin_m))
    lo = max(tee - 12, 0)
    hi = min(tee + 5, suppressor.map.estimator.n_bins)
    background = suppressor.map.background(lo, hi - lo)
    if background.size == 0 or float(background.max()) <= 0.0:
        return None
    return float(lo + int(np.argmax(background)))
