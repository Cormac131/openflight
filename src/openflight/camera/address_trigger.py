"""Ball-at-address state machine for the down-the-line camera trigger.

The camera sits inside the launch monitor, behind the ball on the target
line. That geometry shapes every rule in this module:

* After impact the ball flies *away* from the camera and stays near the image
  centre, only shrinking. "Is there a ball anywhere in frame?" is therefore
  useless; we watch the small region of interest (ROI) where the ball sat at
  address and ask whether *that* region still contains the ball.
* The clubhead sits between the camera and the ball at address and during a
  waggle. A hidden ball must read as "occluded", never as "gone".

Per frame, the locked ROI is classified as one of:

``PRESENT``   NCC against the locked ball template is high (optionally after a
              small nudge search, which re-locks the ball at its new position).
``EMPTY``     No ball-like patch nearby *and* the ROI looks like bare mat.
``OCCLUDED``  No ball visible, but the ROI is not bare mat (club, hand, feet).

A shot is a fast departure: the last ``PRESENT`` frame is followed within
``max_departure_ms`` by an ``EMPTY`` frame, and ``gone_frames`` ``EMPTY`` frames
then accumulate before ``confirm_timeout_ms`` without the ball reappearing. A
hand lifting the ball occludes it for far longer than the departure window,
and a waggle never produces bare mat, so neither triggers.

This module is pure numpy: no camera, serial or threading code. Ball
acquisition (the expensive whole-frame search) is supplied by the caller via
:meth:`BallAddressStateMachine.acquire` so it can run off the frame thread.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Optional

import numpy as np

# Pylint's NumPy inference resolves ndarray values as the np.array callable.
# pylint: disable=no-member

NS_PER_MS = 1_000_000


class AddressState(str, Enum):
    """Lifecycle of one ball at address."""

    IDLE = "idle"
    BALL_PRESENT = "ball_present"
    ADDRESSED = "addressed"
    CANDIDATE_GONE = "candidate_gone"
    TRIGGERED = "triggered"


class RoiClass(str, Enum):
    """Per-frame classification of the locked ball ROI."""

    PRESENT = "present"
    EMPTY = "empty"
    OCCLUDED = "occluded"


@dataclass(frozen=True)
class BallCandidate:
    """A ball found by the acquisition detector (pixel coordinates)."""

    x: float
    y: float
    radius: float


@dataclass(frozen=True)
class AddressTriggerConfig:  # pylint: disable=too-many-instance-attributes
    """Tuning for the address state machine.

    Frame counts assume the 300 fps Global Shutter configuration; time-based
    limits are used wherever dropped frames would otherwise skew behaviour.
    """

    roi_radius_scale: float = 1.6
    min_ball_radius_px: float = 3.0
    stable_acquisitions: int = 3
    acquisition_position_tolerance_px: float = 2.0
    present_ncc: float = 0.6
    # Only used to pick a ball-free history frame as the bare-mat reference.
    absent_ncc: float = 0.35
    search_radius_px: int = 6
    gone_frames: int = 9
    max_departure_ms: float = 20.0
    confirm_timeout_ms: float = 80.0
    empty_mean_tolerance: float = 0.2
    empty_mad: float = 12.0
    mat_std_margin: float = 6.0
    require_address: bool = True
    address_frames: int = 15
    surround_scale: float = 2.5
    surround_change_pixel: float = 25.0
    surround_change_fraction: float = 0.12
    max_occlusion_ms: float = 30_000.0
    stall_gap_ms: float = 100.0
    empty_history: int = 20

    def __post_init__(self) -> None:
        if self.roi_radius_scale < 1.0:
            raise ValueError("roi_radius_scale must be >= 1.0 so the ROI covers the ball")
        if self.stable_acquisitions < 1:
            raise ValueError("stable_acquisitions must be at least 1")
        if not 0.0 < self.absent_ncc < self.present_ncc <= 1.0:
            raise ValueError("require 0 < absent_ncc < present_ncc <= 1")
        if self.search_radius_px < 0:
            raise ValueError("search_radius_px must be non-negative")
        if self.gone_frames < 1:
            raise ValueError("gone_frames must be at least 1")
        if self.max_departure_ms <= 0 or self.confirm_timeout_ms <= 0:
            raise ValueError("departure and confirm windows must be positive")
        if self.address_frames < 1:
            raise ValueError("address_frames must be at least 1")
        if not 0.0 < self.surround_change_fraction <= 1.0:
            raise ValueError("surround_change_fraction must be in (0, 1]")
        if self.surround_scale <= self.roi_radius_scale:
            raise ValueError("surround_scale must exceed roi_radius_scale")
        if self.stall_gap_ms <= 0 or self.max_occlusion_ms <= 0:
            raise ValueError("stall_gap_ms and max_occlusion_ms must be positive")
        if self.empty_history < 0:
            raise ValueError("empty_history must be non-negative")


@dataclass(frozen=True)
class AddressTrigger:
    """A confirmed ball departure, in sensor-clock nanoseconds."""

    impact_sensor_ns: int
    impact_uncertainty_ns: int
    last_present_sensor_ns: int
    first_absent_sensor_ns: int
    confirmed_sensor_ns: int
    ball: BallCandidate
    addressed: bool


@dataclass(frozen=True)
class AddressEvent:
    """A state-machine transition worth logging or acting on."""

    kind: str
    state: AddressState
    sensor_timestamp_ns: int
    detail: dict = field(default_factory=dict)
    trigger: Optional[AddressTrigger] = None


def normalized_cross_correlation(patch: np.ndarray, template: np.ndarray) -> float:
    """Return zero-mean NCC in [-1, 1]; 0 when either input is flat."""
    a = patch.astype(np.float32) - float(patch.mean())
    b = template.astype(np.float32) - float(template.mean())
    denom = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    if denom < 1e-6:
        return 0.0
    return float((a * b).sum() / denom)


def best_ncc_in_window(
    window: np.ndarray,
    template: np.ndarray,
) -> tuple[float, int, int]:
    """Slide ``template`` over ``window``; return (best NCC, row, col) offsets."""
    th, tw = template.shape
    if window.shape[0] < th or window.shape[1] < tw:
        return 0.0, 0, 0
    views = np.lib.stride_tricks.sliding_window_view(window.astype(np.float32), (th, tw))
    t = template.astype(np.float32) - float(template.mean())
    t_energy = float((t * t).sum())
    if t_energy < 1e-6:
        return 0.0, 0, 0
    means = views.mean(axis=(2, 3), keepdims=True)
    centred = views - means
    numer = (centred * t).sum(axis=(2, 3))
    energy = (centred * centred).sum(axis=(2, 3))
    denom = np.sqrt(energy * t_energy)
    scores = np.where(denom > 1e-6, numer / np.maximum(denom, 1e-6), 0.0)
    row, col = divmod(int(np.argmax(scores)), scores.shape[1])
    return float(scores[row, col]), row, col


def _crop(frame: np.ndarray, cx: int, cy: int, half: int) -> Optional[np.ndarray]:
    """Return a (2*half+1)^2 view centred on (cx, cy), or None if off-frame."""
    y0, y1 = cy - half, cy + half + 1
    x0, x1 = cx - half, cx + half + 1
    if y0 < 0 or x0 < 0 or y1 > frame.shape[0] or x1 > frame.shape[1]:
        return None
    return frame[y0:y1, x0:x1]


def _ring_mask(size: int, inner: float, outer: float) -> np.ndarray:
    """Boolean annulus mask centred in a size x size square."""
    centre = (size - 1) / 2.0
    yy, xx = np.mgrid[0:size, 0:size]
    dist = np.hypot(yy - centre, xx - centre)
    return (dist >= inner) & (dist <= outer)


@dataclass
class _Lock:  # pylint: disable=too-many-instance-attributes
    """Everything captured when a ball is locked."""

    cx: int
    cy: int
    radius: float
    half: int
    surround_half: int
    template: np.ndarray
    empty_template: Optional[np.ndarray]
    mat_mean: float
    mat_std: float
    surround_ref: np.ndarray
    surround_mask: np.ndarray
    brightness_scale: float


class BallAddressStateMachine:  # pylint: disable=too-many-instance-attributes
    """Track one ball from placement to departure.

    Thread-safety is the caller's job: :meth:`feed`, :meth:`acquire` and
    :meth:`rearm` must be serialised (``CameraAddressMonitor`` holds a lock).
    """

    def __init__(self, config: Optional[AddressTriggerConfig] = None):
        self.config = config or AddressTriggerConfig()
        # Bare-mat history survives re-arms; everything below is per ball.
        self._empty_frames: Deque[np.ndarray] = deque(maxlen=self.config.empty_history)
        self.state = AddressState.IDLE
        self._lock: Optional[_Lock] = None
        self._stable_count = 0
        self._last_candidate: Optional[BallCandidate] = None
        self._addressed = False
        self._address_count = 0
        self._last_present_ns: Optional[int] = None
        self._first_absent_ns: Optional[int] = None
        self._candidate_start_ns: Optional[int] = None
        self._gone_count = 0
        self._removal_count = 0
        self._last_frame_ns: Optional[int] = None

    # ------------------------------------------------------------------ state

    def _reset_ball(self) -> None:
        """Forget the current ball (mirrors the per-ball fields in __init__)."""
        self.state = AddressState.IDLE
        self._lock = None
        self._stable_count = 0
        self._last_candidate = None
        self._addressed = False
        self._address_count = 0
        self._last_present_ns = None
        self._first_absent_ns = None
        self._candidate_start_ns = None
        self._gone_count = 0
        self._removal_count = 0
        self._last_frame_ns = None

    @property
    def needs_acquisition(self) -> bool:
        """True while waiting for a ball to be placed."""
        return self.state == AddressState.IDLE

    @property
    def locked_ball(self) -> Optional[BallCandidate]:
        """The currently locked ball position, if any."""
        if self._lock is None:
            return None
        return BallCandidate(float(self._lock.cx), float(self._lock.cy), self._lock.radius)

    @property
    def addressed(self) -> bool:
        """Whether the club has been seen at address for the locked ball."""
        return self._addressed

    def rearm(self) -> None:
        """Return to IDLE after a trigger; a *new* ball must be acquired."""
        self._reset_ball()

    # ------------------------------------------------------------ acquisition

    def acquire(
        self,
        frame: np.ndarray,
        sensor_ns: int,
        candidate: Optional[BallCandidate],
        brightness_scale: float = 1.0,
    ) -> list[AddressEvent]:
        """Offer an acquisition-detector result for ``frame`` (IDLE only)."""
        if self.state != AddressState.IDLE:
            return []

        cfg = self.config
        if candidate is None or candidate.radius < cfg.min_ball_radius_px:
            # A ball-free frame is our best reference for "bare mat".
            self._empty_frames.append(np.array(frame, copy=True))
            self._stable_count = 0
            self._last_candidate = None
            return []

        previous = self._last_candidate
        if previous is not None and (
            math.hypot(candidate.x - previous.x, candidate.y - previous.y)
            <= cfg.acquisition_position_tolerance_px
        ):
            self._stable_count += 1
        else:
            self._stable_count = 1
        self._last_candidate = candidate

        if self._stable_count < cfg.stable_acquisitions:
            return []

        lock = self._build_lock(frame, candidate, brightness_scale)
        if lock is None:
            self._stable_count = 0
            return [
                AddressEvent(
                    "rejected_edge",
                    self.state,
                    sensor_ns,
                    {"x": candidate.x, "y": candidate.y, "radius": candidate.radius},
                )
            ]

        self._lock = lock
        self.state = AddressState.BALL_PRESENT
        self._last_present_ns = sensor_ns
        self._last_frame_ns = sensor_ns
        return [
            AddressEvent(
                "locked",
                self.state,
                sensor_ns,
                {
                    "x": lock.cx,
                    "y": lock.cy,
                    "radius": lock.radius,
                    "empty_reference": (
                        "history" if lock.empty_template is not None else "mat_ring"
                    ),
                },
            )
        ]

    def _build_lock(
        self,
        frame: np.ndarray,
        candidate: BallCandidate,
        brightness_scale: float,
    ) -> Optional[_Lock]:
        cfg = self.config
        cx, cy = int(round(candidate.x)), int(round(candidate.y))
        half = int(math.ceil(candidate.radius * cfg.roi_radius_scale))
        surround_half = int(math.ceil(candidate.radius * cfg.surround_scale))
        # The nudge search needs room around the ROI, the surround needs its ring.
        needed = max(half + cfg.search_radius_px, surround_half)
        if _crop(frame, cx, cy, needed) is None:
            return None

        template = np.array(_crop(frame, cx, cy, half), dtype=np.float32)
        surround = np.array(_crop(frame, cx, cy, surround_half), dtype=np.float32)
        size = surround.shape[0]
        surround_mask = _ring_mask(size, inner=half + 1, outer=surround_half)
        mat_ring = _ring_mask(size, inner=candidate.radius * 1.3, outer=candidate.radius * 2.0)
        mat_pixels = surround[mat_ring]

        empty_template = None
        for past in reversed(self._empty_frames):
            patch = _crop(past, cx, cy, half)
            if patch is None:
                continue
            if normalized_cross_correlation(patch, template) < cfg.absent_ncc:
                empty_template = np.array(patch, dtype=np.float32)
                break

        return _Lock(
            cx=cx,
            cy=cy,
            radius=float(candidate.radius),
            half=half,
            surround_half=surround_half,
            template=template,
            empty_template=empty_template,
            mat_mean=float(mat_pixels.mean()) if mat_pixels.size else 0.0,
            mat_std=float(mat_pixels.std()) if mat_pixels.size else 0.0,
            surround_ref=surround,
            surround_mask=surround_mask,
            brightness_scale=max(float(brightness_scale), 1e-6),
        )

    # --------------------------------------------------------- per-frame path

    def classify(self, frame: np.ndarray, brightness_scale: float = 1.0) -> RoiClass:
        """Classify the locked ROI in ``frame`` (re-locking on a small nudge)."""
        lock = self._lock
        if lock is None:
            raise RuntimeError("classify() requires a locked ball")
        cfg = self.config
        gain = max(float(brightness_scale), 1e-6) / lock.brightness_scale

        patch = _crop(frame, lock.cx, lock.cy, lock.half)
        if patch is None:
            return RoiClass.OCCLUDED
        if normalized_cross_correlation(patch, lock.template) >= cfg.present_ncc:
            return RoiClass.PRESENT

        if cfg.search_radius_px > 0:
            window = _crop(frame, lock.cx, lock.cy, lock.half + cfg.search_radius_px)
            if window is not None:
                best, row, col = best_ncc_in_window(window, lock.template)
                if best >= cfg.present_ncc:
                    self._relock(
                        frame,
                        lock.cx + col - cfg.search_radius_px,
                        lock.cy + row - cfg.search_radius_px,
                    )
                    return RoiClass.PRESENT

        # No ball nearby. Decide empty-vs-occluded against the bare-mat
        # reference only: a best-of-many NCC over textured mat routinely
        # scores ~0.4 by chance, so it cannot separate the two.
        if self._looks_empty(patch.astype(np.float32) / gain):
            return RoiClass.EMPTY
        return RoiClass.OCCLUDED

    def _relock(self, frame: np.ndarray, cx: int, cy: int) -> None:
        """Follow a nudged ball; keep the mat reference, refresh the template."""
        lock = self._lock
        assert lock is not None
        moved = math.hypot(cx - lock.cx, cy - lock.cy)
        new_template = _crop(frame, cx, cy, lock.half)
        if new_template is None or _crop(frame, cx, cy, lock.surround_half) is None:
            return
        lock.cx, lock.cy = cx, cy
        lock.template = np.array(new_template, dtype=np.float32)
        # An empty-mat patch from the old position no longer lines up once the
        # ball has moved more than a quarter radius; fall back to ring stats.
        if moved > lock.radius * 0.25:
            lock.empty_template = None

    def _looks_empty(self, patch: np.ndarray) -> bool:
        lock = self._lock
        assert lock is not None
        cfg = self.config
        mean = float(patch.mean())
        if lock.empty_template is not None:
            ref = lock.empty_template
            ref_mean = float(ref.mean())
            mad = float(np.abs((patch - mean) - (ref - ref_mean)).mean())
            rel = abs(mean - ref_mean) / max(ref_mean, 1.0)
            return mad <= cfg.empty_mad and rel <= cfg.empty_mean_tolerance
        rel = abs(mean - lock.mat_mean) / max(lock.mat_mean, 1.0)
        return (
            rel <= cfg.empty_mean_tolerance
            and float(patch.std()) <= lock.mat_std * 1.5 + cfg.mat_std_margin
        )

    def _surround_changed(self, frame: np.ndarray, gain: float) -> bool:
        lock = self._lock
        assert lock is not None
        surround = _crop(frame, lock.cx, lock.cy, lock.surround_half)
        if surround is None:
            return False
        diff = np.abs(surround.astype(np.float32) / gain - lock.surround_ref)
        changed = diff[lock.surround_mask] > self.config.surround_change_pixel
        return float(changed.mean()) >= self.config.surround_change_fraction

    def feed(  # pylint: disable=too-many-return-statements,too-many-branches
        self,
        frame: np.ndarray,
        sensor_ns: int,
        brightness_scale: float = 1.0,
    ) -> list[AddressEvent]:
        """Process one frame; returns events (including a ``trigger``)."""
        if self.state in (AddressState.IDLE, AddressState.TRIGGERED) or self._lock is None:
            return []

        cfg = self.config
        events: list[AddressEvent] = []

        if (
            self._last_frame_ns is not None
            and sensor_ns - self._last_frame_ns > cfg.stall_gap_ms * NS_PER_MS
        ):
            # We cannot reason about what happened during a gap: drop any
            # pending departure. A ball that left during the gap fails the
            # departure window and is eventually reported as lost.
            events.append(
                AddressEvent(
                    "stall",
                    self.state,
                    sensor_ns,
                    {"gap_ms": (sensor_ns - self._last_frame_ns) / NS_PER_MS},
                )
            )
            self._cancel_candidate()
        self._last_frame_ns = sensor_ns

        gain = max(float(brightness_scale), 1e-6) / self._lock.brightness_scale
        roi = self.classify(frame, brightness_scale)

        if roi == RoiClass.PRESENT:
            self._last_present_ns = sensor_ns
            self._first_absent_ns = None
            self._removal_count = 0
            if self.state == AddressState.CANDIDATE_GONE:
                events.append(AddressEvent("candidate_cancelled", self.state, sensor_ns))
                self._cancel_candidate()
            if not self._addressed and self._surround_changed(frame, gain):
                events.extend(self._count_address(sensor_ns))
            return events

        if self._first_absent_ns is None:
            self._first_absent_ns = sensor_ns

        if roi == RoiClass.OCCLUDED:
            if self.state != AddressState.CANDIDATE_GONE and not self._addressed:
                events.extend(self._count_address(sensor_ns))
            events.extend(self._check_candidate_timeout(sensor_ns))
            events.extend(self._check_occlusion_timeout(sensor_ns))
            return events

        # EMPTY
        if self.state != AddressState.CANDIDATE_GONE:
            last_present = self._last_present_ns
            if last_present is None or sensor_ns - last_present > cfg.max_departure_ms * NS_PER_MS:
                # Slow removal (hand, rake) — not a shot. Once bare mat has
                # persisted as long as a shot confirmation, the ball is gone.
                self._removal_count += 1
                if self._removal_count >= cfg.gone_frames:
                    self._reset_ball()
                    events.append(AddressEvent("lost", self.state, sensor_ns, {"cause": "removed"}))
                return events
            self.state = AddressState.CANDIDATE_GONE
            self._candidate_start_ns = sensor_ns
            self._gone_count = 0
            events.append(AddressEvent("candidate_gone", self.state, sensor_ns))

        self._gone_count += 1
        if self._gone_count >= cfg.gone_frames:
            events.append(self._confirm(sensor_ns))
            return events
        events.extend(self._check_candidate_timeout(sensor_ns))
        return events

    def _count_address(self, sensor_ns: int) -> list[AddressEvent]:
        self._address_count += 1
        if self._address_count < self.config.address_frames:
            return []
        self._addressed = True
        if self.state == AddressState.BALL_PRESENT:
            self.state = AddressState.ADDRESSED
        return [AddressEvent("addressed", self.state, sensor_ns)]

    def _resting_state(self) -> AddressState:
        return AddressState.ADDRESSED if self._addressed else AddressState.BALL_PRESENT

    def _cancel_candidate(self) -> None:
        if self.state == AddressState.CANDIDATE_GONE:
            self.state = self._resting_state()
        self._candidate_start_ns = None
        self._gone_count = 0

    def _check_candidate_timeout(self, sensor_ns: int) -> list[AddressEvent]:
        if self.state != AddressState.CANDIDATE_GONE or self._candidate_start_ns is None:
            return []
        if sensor_ns - self._candidate_start_ns <= self.config.confirm_timeout_ms * NS_PER_MS:
            return []
        detail = {"gone_frames": self._gone_count}
        self._cancel_candidate()
        return [AddressEvent("candidate_expired", self.state, sensor_ns, detail)]

    def _check_occlusion_timeout(self, sensor_ns: int) -> list[AddressEvent]:
        # A club soled behind the ball can hide it for many seconds, so the
        # lock survives occlusion; only an implausibly long one (ball swapped
        # while hidden) drops it.
        last_present = self._last_present_ns
        if last_present is None:
            return []
        if sensor_ns - last_present <= self.config.max_occlusion_ms * NS_PER_MS:
            return []
        self._reset_ball()
        return [AddressEvent("lost", self.state, sensor_ns, {"cause": "occlusion_timeout"})]

    def _confirm(self, sensor_ns: int) -> AddressEvent:
        lock = self._lock
        assert lock is not None
        last_present = self._last_present_ns
        first_absent = self._first_absent_ns
        assert last_present is not None and first_absent is not None
        ball = BallCandidate(float(lock.cx), float(lock.cy), lock.radius)
        addressed = self._addressed

        if self.config.require_address and not addressed:
            self._reset_ball()
            return AddressEvent(
                "rejected_not_addressed",
                self.state,
                sensor_ns,
                {"x": ball.x, "y": ball.y},
            )

        trigger = AddressTrigger(
            impact_sensor_ns=(last_present + first_absent) // 2,
            impact_uncertainty_ns=(first_absent - last_present) // 2,
            last_present_sensor_ns=last_present,
            first_absent_sensor_ns=first_absent,
            confirmed_sensor_ns=sensor_ns,
            ball=ball,
            addressed=addressed,
        )
        self.state = AddressState.TRIGGERED
        return AddressEvent(
            "trigger",
            self.state,
            sensor_ns,
            {
                "impact_uncertainty_ms": trigger.impact_uncertainty_ns / NS_PER_MS,
                "gone_frames": self._gone_count,
            },
            trigger=trigger,
        )
