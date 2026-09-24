"""Experimental rear-camera horizontal ball-flight reconstruction.

Camera centroids provide ball bearing. IWR6843 range is the preferred metric
depth source; apparent regulation-ball size provides a lower-confidence camera-
only fallback. OPS ball speed gates target identity when it is available; a
frozen speed band from the active :class:`BallSearchProfile` gates it
otherwise. IWR horizontal remains an independent comparison and fallback.

Two search profiles exist. ``FLIGHT_SEARCH`` follows a struck ball rising out
of the frame within 50 ms of the trigger. ``PUTT_SEARCH`` follows a ball
rolling along the ground for several hundred milliseconds, with no radar speed
and depth taken from the apparent ball size, and reports ball speed and the
horizontal start line.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace

import numpy as np
from scipy import ndimage

from openflight.camera.club_motion import (
    BALL_DIAMETER_MM,
    ReferenceBall,
    detect_reference_ball,
)
from openflight.camera.geometry import deroll_normalized_offsets

MPH_PER_MS = 2.23694
PARAMETER_SWEEP_SIZE = 27
MAX_IWR_FALLBACK_ABS_DEG = 20.0
MIN_PATH_POINTS = 4


@dataclass(frozen=True)
class BallSearchProfile:
    """Frozen search bands for one class of ball motion.

    ``post_trigger_frames`` and ``frame_stride`` define which captured frames
    are searched after the trigger. ``step_dy_band_px`` is the per-frame image
    vertical displacement accepted when linking candidates (negative is up in
    the image), scaled by the frame gap. ``speed_band_mph`` gates the fitted
    speed when no OPS ball speed is supplied. ``min_step_s`` spaces the step
    statistics so sub-pixel per-frame motion does not read as scatter.
    """

    name: str
    post_trigger_frames: int
    frame_stride: int
    seed_nodes: int
    max_step_dx_px: float
    step_dy_band_px: tuple[float, float]
    max_candidate_area_px: int
    speed_band_mph: tuple[float, float]
    vertical_band_deg: tuple[float, float]
    vertical_prior_deg: float | None
    min_step_s: float
    # Bright pixels inside this many anchor diameters of the reference ball
    # bypass background subtraction, so a ball that has barely left its own
    # background footprint is still seen whole. Zero keeps subtraction everywhere.
    anchor_exempt_diameters: float
    # Measure ball diameters at half maximum instead of at the detection
    # threshold. Size-based depth is the only depth source for a putt, and a
    # threshold-dependent edge band biases a shrinking ball toward the camera.
    refine_diameter: bool
    # Beam-search score penalty per node between the window start and a path's
    # first point. Equal-length chains otherwise tie, and over a long window
    # the beam keeps whichever chains span the fewest nodes.
    late_start_penalty: float

    def __post_init__(self) -> None:
        if self.post_trigger_frames < MIN_PATH_POINTS:
            raise ValueError("post_trigger_frames must allow at least four path points")
        if self.frame_stride < 1 or self.seed_nodes < 1:
            raise ValueError("frame_stride and seed_nodes must be positive")
        if self.step_dy_band_px[0] > self.step_dy_band_px[1]:
            raise ValueError("step_dy_band_px must be ordered (low, high)")
        if not 0.0 < self.speed_band_mph[0] < self.speed_band_mph[1]:
            raise ValueError("speed_band_mph must be ordered and positive")
        if self.vertical_band_deg[0] > self.vertical_band_deg[1]:
            raise ValueError("vertical_band_deg must be ordered (low, high)")
        if min(self.min_step_s, self.anchor_exempt_diameters, self.late_start_penalty) < 0.0:
            raise ValueError(
                "min_step_s, anchor_exempt_diameters and late_start_penalty must be non-negative"
            )

    def speed_band(self, ops_ball_speed_mph: float | None) -> tuple[float, float]:
        """Return the accepted speed band, preferring the OPS ratio gate."""
        if ops_ball_speed_mph is None:
            return self.speed_band_mph
        return 0.5 * ops_ball_speed_mph, 1.5 * ops_ball_speed_mph


FLIGHT_SEARCH = BallSearchProfile(
    name="flight",
    post_trigger_frames=15,
    frame_stride=1,
    seed_nodes=5,
    max_step_dx_px=30.0,
    step_dy_band_px=(-38.0, -0.5),
    max_candidate_area_px=400,
    speed_band_mph=(15.0, 220.0),
    vertical_band_deg=(-5.0, 55.0),
    vertical_prior_deg=None,
    min_step_s=0.0,
    anchor_exempt_diameters=0.0,
    refine_diameter=False,
    late_start_penalty=0.0,
)

# A putt at 1 to 15 mph moves well under a pixel per frame at 300 fps, so the
# search spans 400 ms on every third frame, links flat or gently rising image
# motion, and accepts a ball that still overlaps its own background footprint.
PUTT_SEARCH = BallSearchProfile(
    name="putt",
    post_trigger_frames=120,
    frame_stride=3,
    seed_nodes=12,
    max_step_dx_px=30.0,
    step_dy_band_px=(-12.0, 4.0),
    max_candidate_area_px=800,
    speed_band_mph=(0.5, 15.0),
    vertical_band_deg=(-10.0, 10.0),
    vertical_prior_deg=0.0,
    min_step_s=0.1,
    anchor_exempt_diameters=1.5,
    refine_diameter=True,
    late_start_penalty=5.0,
)


@dataclass(frozen=True)
class BallCandidate:
    """One ball-like connected component in a camera frame."""

    x: float
    y: float
    area: int
    width: int
    height: int
    fill: float
    circularity: float
    mean_intensity: float
    # Half-maximum apparent diameter, measured independently of the detection
    # threshold. ``None`` falls back to the equivalent-area diameter.
    diameter_px: float | None = None


def _candidate_diameter_px(candidate: BallCandidate) -> float:
    """Apparent ball diameter, preferring the threshold-independent measurement."""
    if candidate.diameter_px is not None:
        return candidate.diameter_px
    return math.sqrt(4.0 * candidate.area / math.pi)


@dataclass(frozen=True)
class CameraBallGeometry:
    """Measured geometry shared by the rear camera and IWR6843."""

    camera_height_m: float
    radar_height_m: float
    tee_range_m: float
    ball_height_m: float
    # Camera optical-center position relative to radar center. Positive is
    # target-right when viewed from behind the sensors looking downrange.
    camera_lateral_offset_m: float = 0.0
    horizontal_offset_deg: float = 0.0
    # Convert saved-image horizontal pixels back to physical target direction.
    # Mirrored operator previews use -1; unmirrored captures use +1.
    horizontal_pixel_sign: float = 1.0
    roll_correction_deg: float = 0.0
    ball_diameter_m: float = BALL_DIAMETER_MM / 1000.0
    image_width_px: int = 640
    image_height_px: int = 400

    @classmethod
    def from_camera_measurements(
        cls,
        *,
        camera_height_m: float,
        ball_forward_m: float,
        ball_height_m: float = BALL_DIAMETER_MM / 2000.0,
        **overrides,
    ) -> "CameraBallGeometry":
        """Build geometry from a tape measure instead of the IWR6843 tee calibration.

        The virtual radar is placed at the camera, so ``ball_forward_m`` is the
        horizontal distance from the camera to the ball at address and
        ``ball_height_m`` defaults to a ball resting on the ground. Optional
        overrides are the remaining dataclass fields (lateral offset, yaw and
        roll corrections, pixel sign, image size).
        """
        if camera_height_m <= 0.0 or ball_forward_m <= 0.0:
            raise ValueError("camera_height_m and ball_forward_m must be positive")
        if ball_height_m < 0.0:
            raise ValueError("ball_height_m must be non-negative")
        if "radar_height_m" in overrides or "tee_range_m" in overrides:
            raise TypeError("radar_height_m and tee_range_m are derived from the camera")
        return cls(
            camera_height_m=camera_height_m,
            radar_height_m=camera_height_m,
            tee_range_m=math.hypot(ball_forward_m, ball_height_m - camera_height_m),
            ball_height_m=ball_height_m,
            **overrides,
        )

    @property
    def ball_forward_m(self) -> float:
        """Forward radar-to-ball distance derived from tee slant range."""
        vertical = self.ball_height_m - self.radar_height_m
        return math.sqrt(max(self.tee_range_m**2 - vertical**2, 1e-9))

    @property
    def camera_origin(self) -> np.ndarray:
        """Camera origin in the radar-centered world coordinate system."""
        return np.array([self.camera_lateral_offset_m, 0.0, self.camera_height_m])


@dataclass(frozen=True)
class CameraBallEstimate:
    """Consensus result from the camera/IWR/OPS ball-flight estimator.

    ``horizontal_deg`` is the launch direction (the start line for a putt) and
    ``speed_mph`` the fitted ball speed. ``speed_error_mph`` is ``None`` when
    no OPS ball speed was supplied to compare against.
    """

    status: str
    confidence_tier: str = "withheld"
    horizontal_deg: float | None = None
    vertical_deg: float | None = None
    support: int = 0
    support_pct: float = 0.0
    parameter_mad_deg: float | None = None
    window_mad_deg: float | None = None
    speed_mph: float | None = None
    speed_error_mph: float | None = None
    n_points: int = 0
    first_frame: int | None = None
    last_frame: int | None = None
    depth_source: str | None = None
    search_profile: str | None = None


@dataclass(frozen=True)
class HorizontalFusionDecision:
    """Selected horizontal result plus independent sensor provenance."""

    selected_deg: float | None
    source: str | None
    confidence: float | None
    status: str
    iwr_horizontal_deg: float | None
    camera_horizontal_deg: float | None
    camera_iwr_delta_deg: float | None


@dataclass(frozen=True)
class _PathEstimate:
    horizontal_deg: float
    vertical_deg: float
    speed_mph: float
    speed_error_mph: float | None
    fit_median_m: float
    step_speed_mad_mph: float
    window_mad_deg: float
    n_points: int
    first_frame: int
    last_frame: int


def _camera_model(
    anchor: ReferenceBall,
    geometry: CameraBallGeometry,
) -> tuple[float, float, np.ndarray]:
    """Infer focal scale and pose from the stationary regulation-size ball."""
    center_x = geometry.image_width_px / 2.0
    center_y = geometry.image_height_px / 2.0
    camera_ball_range = math.sqrt(
        geometry.camera_lateral_offset_m**2
        + geometry.ball_forward_m**2
        + (geometry.ball_height_m - geometry.camera_height_m) ** 2
    )
    focal_px = anchor.diameter_px * camera_ball_range / geometry.ball_diameter_m
    ball_x = geometry.horizontal_pixel_sign * (anchor.x - center_x) / focal_px
    ball_z = -(anchor.y - center_y) / focal_px
    _ball_x, ball_z = deroll_normalized_offsets(
        ball_x,
        ball_z,
        geometry.roll_correction_deg,
    )
    pitch = math.atan2(
        geometry.ball_height_m - geometry.camera_height_m,
        geometry.ball_forward_m,
    ) - math.atan2(ball_z, 1.0)
    radar_from_camera = geometry.camera_origin - np.array([0.0, 0.0, geometry.radar_height_m])
    return focal_px, pitch, radar_from_camera


def _project(
    candidate: BallCandidate,
    radar_range_m: float,
    *,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
) -> np.ndarray | None:
    _focal_px, _pitch, radar_from_camera = model
    ray = _camera_ray(candidate, model=model, geometry=geometry)
    ray_offset = float(ray @ radar_from_camera)
    discriminant = ray_offset**2 - (float(radar_from_camera @ radar_from_camera) - radar_range_m**2)
    if discriminant < 0.0:
        return None
    distance = -ray_offset + math.sqrt(discriminant)
    if distance <= 0.0:
        return None
    return geometry.camera_origin + distance * ray


def _camera_ray(
    candidate: BallCandidate,
    *,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
) -> np.ndarray:
    """Return the unit camera ray through a detected ball centroid."""
    focal_px, pitch, _radar_from_camera = model
    image_x = (
        geometry.horizontal_pixel_sign * (candidate.x - geometry.image_width_px / 2.0) / focal_px
    )
    image_z = -(candidate.y - geometry.image_height_px / 2.0) / focal_px
    image_x, image_z = deroll_normalized_offsets(
        image_x,
        image_z,
        geometry.roll_correction_deg,
    )
    ray = np.array(
        [
            image_x,
            math.cos(pitch) - image_z * math.sin(pitch),
            math.sin(pitch) + image_z * math.cos(pitch),
        ]
    )
    ray /= np.linalg.norm(ray)
    return ray


def _project_from_ball_size(
    candidate: BallCandidate,
    *,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
) -> np.ndarray | None:
    """Project a regulation ball using apparent diameter as camera depth."""
    measured_diameter_px = _candidate_diameter_px(candidate)
    if measured_diameter_px <= 0.0:
        return None
    camera_range_m = model[0] * geometry.ball_diameter_m / measured_diameter_px
    if not 0.25 <= camera_range_m <= 15.0:
        return None
    ray = _camera_ray(candidate, model=model, geometry=geometry)
    return geometry.camera_origin + camera_range_m * ray


def _candidates(
    frame: np.ndarray,
    background: np.ndarray,
    anchor: ReferenceBall,
    *,
    bright_threshold: int,
    difference_threshold: int,
    min_area: int,
    max_area: int = FLIGHT_SEARCH.max_candidate_area_px,
    anchor_exempt_radius_px: float = 0.0,
    refine_diameter: bool = False,
) -> list[BallCandidate]:
    try:
        import cv2  # noqa: PLC0415  pylint: disable=import-outside-toplevel
    except ImportError as exc:  # pragma: no cover - optional hardware dependency
        raise RuntimeError("camera ball flight requires OpenCV") from exc

    difference = cv2.subtract(frame, background)
    changed = difference > difference_threshold
    if anchor_exempt_radius_px > 0.0:
        changed = changed | _anchor_disk(frame.shape, anchor, anchor_exempt_radius_px)
    mask = ((frame > bright_threshold) & changed).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    found: list[BallCandidate] = []
    for label in range(1, count):
        _, _, width, height, area = stats[label]
        x, y = centroids[label]
        aspect = width / max(height, 1)
        fill = area / max(width * height, 1)
        if not (
            min_area <= area <= max_area
            and 0.35 <= aspect <= 2.8
            and fill >= 0.18
            and abs(x - anchor.x) < 160
            and 10 < y < anchor.y + 15
        ):
            continue
        left = int(stats[label][cv2.CC_STAT_LEFT])
        top = int(stats[label][cv2.CC_STAT_TOP])
        roi = (labels[top : top + height, left : left + width] == label).astype(np.uint8)
        contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        perimeter = sum(cv2.arcLength(contour, True) for contour in contours)
        circularity = 4.0 * math.pi * area / perimeter**2 if perimeter > 0.0 else 0.0
        pixels = frame[top : top + height, left : left + width][roi > 0]
        found.append(
            BallCandidate(
                x=float(x),
                y=float(y),
                area=int(area),
                width=int(width),
                height=int(height),
                fill=float(fill),
                circularity=float(circularity),
                mean_intensity=float(np.mean(pixels)),
                diameter_px=(
                    _half_max_diameter_px(frame, labels == label) if refine_diameter else None
                ),
            )
        )
    return found


def _half_max_diameter_px(
    frame: np.ndarray,
    member: np.ndarray,
    *,
    pad_px: int = 3,
    min_contrast: float = 40.0,
) -> float | None:
    """Equivalent diameter of the pixels above half maximum around one component.

    The floor is the median of the padded box's outer ring outside the
    component and the peak the component's 90th percentile, so the edge band
    counted does not depend on the detection threshold. ``None`` when the
    component has too little local contrast to place a half-maximum edge.
    """
    ys, xs = np.nonzero(member)
    if len(ys) == 0:
        return None
    y0 = max(0, int(ys.min()) - pad_px)
    y1 = min(frame.shape[0], int(ys.max()) + pad_px + 1)
    x0 = max(0, int(xs.min()) - pad_px)
    x1 = min(frame.shape[1], int(xs.max()) + pad_px + 1)
    patch = frame[y0:y1, x0:x1].astype(np.float32)
    patch_member = member[y0:y1, x0:x1]
    ring = np.ones(patch.shape, dtype=bool)
    ring[1:-1, 1:-1] = False
    floor_pixels = patch[ring & ~patch_member]
    if len(floor_pixels) == 0:
        return None
    floor = float(np.median(floor_pixels))
    peak = float(np.percentile(patch[patch_member], 90.0))
    if peak - floor < min_contrast:
        return None
    near = ndimage.binary_dilation(patch_member, iterations=2)
    area = int(np.count_nonzero(near & (patch >= 0.5 * (floor + peak))))
    return math.sqrt(4.0 * area / math.pi) if area > 0 else None


def _refine_anchor_diameter(
    background: np.ndarray,
    anchor: ReferenceBall,
    *,
    bright_threshold: int,
    search_radius_px: float,
) -> ReferenceBall:
    """Re-measure the reference ball at half maximum so it matches the candidates."""
    member = (background > bright_threshold) & _anchor_disk(
        background.shape, anchor, search_radius_px
    )
    labels, count = ndimage.label(member)
    if count == 0:
        return anchor
    row = min(max(int(round(anchor.y)), 0), background.shape[0] - 1)
    column = min(max(int(round(anchor.x)), 0), background.shape[1] - 1)
    label = int(labels[row, column])
    if label == 0:
        # Nearest labelled pixel to the anchor centre.
        ys, xs = np.nonzero(labels)
        nearest = int(np.argmin((xs - anchor.x) ** 2 + (ys - anchor.y) ** 2))
        label = int(labels[ys[nearest], xs[nearest]])
    refined = _half_max_diameter_px(background, labels == label)
    if refined is None or not 0.5 * anchor.diameter_px <= refined <= 2.0 * anchor.diameter_px:
        return anchor
    return replace(anchor, diameter_px=refined)


def _anchor_disk(shape: tuple[int, ...], anchor: ReferenceBall, radius_px: float) -> np.ndarray:
    """Boolean mask of the pixels within ``radius_px`` of the reference ball."""
    ys, xs = np.ogrid[: shape[0], : shape[1]]
    return (xs - anchor.x) ** 2 + (ys - anchor.y) ** 2 <= radius_px**2


def _rough_path_score(
    path: list[tuple[int, BallCandidate]],
    late_start_penalty: float = 0.0,
) -> float:
    """Scalar beam-search score: long, straight chains that start early win."""
    late_start = late_start_penalty * path[0][0]
    if len(path) < 3:
        return 20.0 * len(path) - late_start
    steps = [
        ((second.x - first.x) / (j - i), (second.y - first.y) / (j - i))
        for (i, first), (j, second) in zip(path, path[1:])
    ]
    median_x = statistics.median(step[0] for step in steps)
    median_y = statistics.median(step[1] for step in steps)
    dispersion = statistics.median(
        math.hypot(step_x - median_x, step_y - median_y) for step_x, step_y in steps
    )
    return 20.0 * len(path) - 2.0 * dispersion - late_start


def _pixel_paths(
    nodes: list[list[BallCandidate]],
    anchor: ReferenceBall,
    search: BallSearchProfile = FLIGHT_SEARCH,
) -> list[list[tuple[int, BallCandidate]]]:
    """Beam-search candidate chains whose per-node image steps fit the profile."""
    dy_low, dy_high = search.step_dy_band_px

    def score(path: list[tuple[int, BallCandidate]]) -> float:
        return _rough_path_score(path, search.late_start_penalty)

    all_paths: list[list[tuple[int, BallCandidate]]] = []
    frontier: list[list[tuple[int, BallCandidate]]] = []
    for frame in range(min(search.seed_nodes, len(nodes))):
        for candidate in nodes[frame]:
            if math.hypot(candidate.x - anchor.x, candidate.y - anchor.y) <= 70.0:
                frontier.append([(frame, candidate)])
    all_paths.extend(frontier)
    for _ in range(len(nodes)):
        extended: list[list[tuple[int, BallCandidate]]] = []
        for path in frontier:
            previous_frame, previous = path[-1]
            for frame in range(previous_frame + 1, min(len(nodes), previous_frame + 3)):
                gap = frame - previous_frame
                for candidate in nodes[frame]:
                    delta_x = candidate.x - previous.x
                    delta_y = candidate.y - previous.y
                    if (
                        abs(delta_x) <= search.max_step_dx_px * gap
                        and dy_low * gap <= delta_y <= dy_high * gap
                    ):
                        extended.append([*path, (frame, candidate)])
        if not extended:
            break
        extended.sort(key=score, reverse=True)
        frontier = extended[:150]
        all_paths.extend(frontier)
    viable = [path for path in all_paths if len(path) >= MIN_PATH_POINTS]
    viable.sort(key=score, reverse=True)
    return viable[:120]


def _robust_velocity(times: np.ndarray, positions: np.ndarray) -> tuple[np.ndarray, float]:
    slopes = []
    for first in range(len(times)):
        for second in range(first + 1, len(times)):
            delta = times[second] - times[first]
            if delta > 0.0:
                slopes.append((positions[second] - positions[first]) / delta)
    velocity = np.median(slopes, axis=0)
    intercept = np.median(positions - times[:, None] * velocity, axis=0)
    residual = np.linalg.norm(positions - (intercept + times[:, None] * velocity), axis=1)
    return velocity, float(np.median(residual))


def _step_pairs(times: np.ndarray, min_step_s: float) -> list[tuple[int, int]]:
    """Index pairs for step statistics: consecutive, or non-overlapping spans."""
    if min_step_s <= 0.0:
        return [(index, index + 1) for index in range(len(times) - 1)]
    pairs: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(times)):
        if times[index] - times[start] >= min_step_s:
            pairs.append((start, index))
            start = index
    if not pairs:
        return [(0, len(times) - 1)]
    return pairs


def _horizontal(velocity: np.ndarray) -> float:
    """Return motion direction relative to the camera optical target line."""
    angle = math.atan2(float(velocity[0]), float(velocity[1]))
    return (math.degrees(angle) + 180.0) % 360.0 - 180.0


def _apply_horizontal_offset(angle_deg: float, offset_deg: float) -> float:
    """Apply a measured setup yaw correction while preserving angle wrapping."""
    return (angle_deg + offset_deg + 180.0) % 360.0 - 180.0


def _path_estimate(
    *,
    path: list[tuple[int, BallCandidate]],
    frame_indices: list[int],
    timestamps_ns: np.ndarray,
    trigger_ns: int,
    range_evidence,
    ops_ball_speed_mph: float | None,
    iwr_vertical_deg: float | None,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
    thresholds: tuple[int, int, int],  # retained for replay/debug provenance
    search: BallSearchProfile = FLIGHT_SEARCH,
) -> tuple[float, _PathEstimate] | None:
    del thresholds
    _focal_px, _pitch, _radar_from_camera = model
    times: list[float] = []
    positions: list[np.ndarray] = []
    actual_frames: list[int] = []
    used_candidates: list[BallCandidate] = []
    for relative_frame, candidate in path:
        frame = frame_indices[relative_frame]
        relative_time = (int(timestamps_ns[frame]) - trigger_ns) / 1e9
        if range_evidence is None:
            position = _project_from_ball_size(candidate, model=model, geometry=geometry)
        else:
            radar_range = float(
                range_evidence.track.range_at(
                    range_evidence.impact_t_s + relative_time,
                    range_evidence.geometry.range_res_m,
                )
            )
            position = _project(candidate, radar_range, model=model, geometry=geometry)
        if position is not None:
            times.append(relative_time)
            positions.append(position)
            actual_frames.append(frame)
            used_candidates.append(candidate)
    if len(positions) < MIN_PATH_POINTS:
        return None

    times_array = np.asarray(times)
    positions_array = np.stack(positions)
    velocity, fit_median = _robust_velocity(times_array, positions_array)
    horizontal = _apply_horizontal_offset(
        _horizontal(velocity),
        geometry.horizontal_offset_deg,
    )
    vertical = math.degrees(
        math.atan2(float(velocity[2]), math.hypot(float(velocity[0]), float(velocity[1])))
    )
    speed = float(np.linalg.norm(velocity) * MPH_PER_MS)
    step_pairs = _step_pairs(times_array, search.min_step_s)
    step_velocity = np.stack(
        [
            (positions_array[second] - positions_array[first])
            / (times_array[second] - times_array[first])
            for first, second in step_pairs
        ]
    )
    step_speeds = np.linalg.norm(step_velocity, axis=1) * MPH_PER_MS
    step_speed_mad = float(np.median(np.abs(step_speeds - np.median(step_speeds))))
    step_angles = np.asarray(
        [
            _apply_horizontal_offset(_horizontal(step), geometry.horizontal_offset_deg)
            for step in step_velocity
        ]
    )
    window_mad = float(np.median(np.abs(step_angles - horizontal)))
    shape = np.asarray(
        [abs(math.log(candidate.width / max(candidate.height, 1))) for candidate in used_candidates]
    )
    shape_median = float(np.median(shape))
    fill_median = float(np.median([candidate.fill for candidate in used_candidates]))
    circularity_median = float(np.median([candidate.circularity for candidate in used_candidates]))
    intensity_median = float(np.median([candidate.mean_intensity for candidate in used_candidates]))
    camera_origin = np.array([0.0, 0.0, geometry.camera_height_m])
    camera_ranges = np.linalg.norm(positions_array - camera_origin, axis=1)
    expected_diameter = model[0] * geometry.ball_diameter_m / camera_ranges
    measured_diameter = np.asarray(
        [_candidate_diameter_px(candidate) for candidate in used_candidates]
    )
    size_ratio = measured_diameter / expected_diameter
    size_ratio_median = float(np.median(size_ratio))
    size_ratio_mad = float(np.median(np.abs(size_ratio - size_ratio_median)))
    speed_low, speed_high = search.speed_band(ops_ball_speed_mph)
    vertical_low, vertical_high = search.vertical_band_deg
    if not (
        -30.0 <= horizontal <= 30.0
        and vertical_low <= vertical <= vertical_high
        and speed_low <= speed <= speed_high
        and shape_median <= 0.55
        and fill_median >= 0.5
        and circularity_median >= 0.5
        and intensity_median >= 195.0
        and 0.45 <= size_ratio_median <= 2.5
        and size_ratio_mad <= 0.75
    ):
        return None

    expected_vertical = (
        iwr_vertical_deg if iwr_vertical_deg is not None else search.vertical_prior_deg
    )
    vertical_prior = abs(vertical - expected_vertical) if expected_vertical is not None else 0.0
    speed_error = speed - ops_ball_speed_mph if ops_ball_speed_mph is not None else None
    score = (
        10.0 * len(positions_array)
        - 400.0 * fit_median
        - (0.45 * abs(speed_error) if speed_error is not None else 0.0)
        - 0.35 * step_speed_mad
        - 0.5 * vertical_prior
        - 6.0 * window_mad
        - 8.0 * shape_median
        + 6.0 * fill_median
        + 4.0 * circularity_median
        - 4.0 * size_ratio_mad
        - 2.0 * (actual_frames[0] - frame_indices[0])
    )
    return score, _PathEstimate(
        horizontal_deg=horizontal,
        vertical_deg=vertical,
        speed_mph=speed,
        speed_error_mph=speed_error,
        fit_median_m=fit_median,
        step_speed_mad_mph=step_speed_mad,
        window_mad_deg=window_mad,
        n_points=len(positions_array),
        first_frame=actual_frames[0],
        last_frame=actual_frames[-1],
    )


def _confidence_tier(support: int, parameter_mad: float, window_mad: float) -> str:
    """Map detector consensus and local trajectory coherence to a confidence tier."""
    stable_consensus = parameter_mad <= 1.0
    if support >= 9 and stable_consensus and window_mad <= 0.5:
        return "high"
    if support >= 2 and stable_consensus and window_mad <= 1.5:
        return "experimental"
    return "withheld"


def estimate_camera_ball_flight(
    frames: np.ndarray,
    timestamps_ns: np.ndarray,
    *,
    trigger_ns: int,
    range_evidence,
    geometry: CameraBallGeometry,
    ops_ball_speed_mph: float | None,
    iwr_vertical_deg: float | None = None,
    ball_tracker=None,
    search: BallSearchProfile = FLIGHT_SEARCH,
) -> CameraBallEstimate:
    """Estimate horizontal direction and speed with a frozen detector-consensus sweep.

    ``ops_ball_speed_mph`` gates target identity when the OPS243 saw the ball;
    pass ``None`` (a putt, or an OPS miss) to gate on the profile's speed band
    instead. ``search`` selects the flight or putting search bands.
    """
    if frames.ndim != 3 or len(frames) < MIN_PATH_POINTS or len(timestamps_ns) != len(frames):
        return CameraBallEstimate("rejected_invalid_camera_frames")
    try:
        anchor = detect_reference_ball(frames)
    except ValueError:
        fallback = getattr(ball_tracker, "fallback", None)
        anchor = fallback() if fallback is not None else None
        if anchor is None:
            return CameraBallEstimate("rejected_reference_ball_not_found")
    else:
        if ball_tracker is not None:
            resolver = getattr(ball_tracker, "resolve_stable", ball_tracker.resolve)
            anchor, _anchor_source = resolver(anchor)
    if not 9.0 <= anchor.diameter_px <= 30.0:
        return CameraBallEstimate("rejected_implausible_reference_ball")

    background = np.median(frames[: min(20, len(frames))], axis=0).astype(np.uint8)
    if search.refine_diameter:
        anchor = _refine_anchor_diameter(
            background,
            anchor,
            bright_threshold=115,
            search_radius_px=max(search.anchor_exempt_diameters, 1.0) * anchor.diameter_px,
        )
    model = _camera_model(anchor, geometry)
    trigger_frame = int(np.argmin(np.abs(timestamps_ns.astype(np.int64) - trigger_ns)))
    frame_indices = list(
        range(
            trigger_frame,
            min(len(frames), trigger_frame + search.post_trigger_frames),
            search.frame_stride,
        )
    )
    if len(frame_indices) < MIN_PATH_POINTS:
        return CameraBallEstimate("rejected_insufficient_post_trigger_frames")
    anchor_exempt_radius_px = search.anchor_exempt_diameters * anchor.diameter_px

    def collect(depth_evidence) -> list[_PathEstimate]:
        found: list[_PathEstimate] = []
        for bright in (100, 115, 130):
            for difference in (12, 18, 24):
                for min_area in (5, 10, 20):
                    nodes = [
                        _candidates(
                            frames[frame],
                            background,
                            anchor,
                            bright_threshold=bright,
                            difference_threshold=difference,
                            min_area=min_area,
                            max_area=search.max_candidate_area_px,
                            anchor_exempt_radius_px=anchor_exempt_radius_px,
                            refine_diameter=search.refine_diameter,
                        )
                        for frame in frame_indices
                    ]
                    options = [
                        result
                        for path in _pixel_paths(nodes, anchor, search)
                        if (
                            result := _path_estimate(
                                path=path,
                                frame_indices=frame_indices,
                                timestamps_ns=timestamps_ns,
                                trigger_ns=trigger_ns,
                                range_evidence=depth_evidence,
                                ops_ball_speed_mph=ops_ball_speed_mph,
                                iwr_vertical_deg=iwr_vertical_deg,
                                model=model,
                                geometry=geometry,
                                thresholds=(bright, difference, min_area),
                                search=search,
                            )
                        )
                        is not None
                    ]
                    if options:
                        found.append(max(options, key=lambda item: item[0])[1])
        return found

    depth_source = "iwr_range" if range_evidence is not None else "camera_size"
    estimates = collect(range_evidence)
    if range_evidence is not None:
        primary_tier = "withheld"
        if estimates:
            primary_horizontal = np.asarray([estimate.horizontal_deg for estimate in estimates])
            primary_median = float(np.median(primary_horizontal))
            primary_tier = _confidence_tier(
                len(estimates),
                float(np.median(np.abs(primary_horizontal - primary_median))),
                float(np.median([estimate.window_mad_deg for estimate in estimates])),
            )
        if primary_tier == "withheld":
            camera_only = collect(None)
            if camera_only:
                camera_horizontal = np.asarray(
                    [estimate.horizontal_deg for estimate in camera_only]
                )
                camera_median = float(np.median(camera_horizontal))
                camera_tier = _confidence_tier(
                    len(camera_only),
                    float(np.median(np.abs(camera_horizontal - camera_median))),
                    float(np.median([estimate.window_mad_deg for estimate in camera_only])),
                )
                if camera_tier != "withheld":
                    depth_source = "camera_size"
                    estimates = camera_only

    if not estimates:
        return CameraBallEstimate("rejected_no_stable_path", search_profile=search.name)
    speed_errors = [
        estimate.speed_error_mph for estimate in estimates if estimate.speed_error_mph is not None
    ]
    horizontal = np.asarray([estimate.horizontal_deg for estimate in estimates])
    median_horizontal = float(np.median(horizontal))
    parameter_mad = float(np.median(np.abs(horizontal - median_horizontal)))
    window_mad = float(np.median([estimate.window_mad_deg for estimate in estimates]))
    tier = _confidence_tier(len(estimates), parameter_mad, window_mad)
    if depth_source == "camera_size" and tier == "high":
        tier = "experimental"
    representative = min(estimates, key=lambda item: abs(item.horizontal_deg - median_horizontal))
    return CameraBallEstimate(
        status=(
            "accepted_camera_only"
            if tier != "withheld" and depth_source == "camera_size"
            else "accepted"
            if tier != "withheld"
            else "rejected_unstable_consensus"
        ),
        confidence_tier=tier,
        horizontal_deg=median_horizontal if tier != "withheld" else None,
        vertical_deg=float(np.median([estimate.vertical_deg for estimate in estimates])),
        support=len(estimates),
        support_pct=100.0 * len(estimates) / PARAMETER_SWEEP_SIZE,
        parameter_mad_deg=parameter_mad,
        window_mad_deg=window_mad,
        speed_mph=float(np.median([estimate.speed_mph for estimate in estimates])),
        speed_error_mph=float(np.median(speed_errors)) if speed_errors else None,
        n_points=representative.n_points,
        first_frame=representative.first_frame,
        last_frame=representative.last_frame,
        depth_source=depth_source,
        search_profile=search.name,
    )


def estimate_camera_putt(
    frames: np.ndarray,
    timestamps_ns: np.ndarray,
    *,
    trigger_ns: int,
    geometry: CameraBallGeometry,
    ball_tracker=None,
) -> CameraBallEstimate:
    """Estimate putt ball speed and start line from the rear camera alone.

    Depth comes from the apparent ball size, so ``geometry`` is normally built
    with :meth:`CameraBallGeometry.from_camera_measurements`. The returned
    ``speed_mph`` is the ball speed and ``horizontal_deg`` is the start line
    relative to the camera target line (positive is target-right).
    """
    return estimate_camera_ball_flight(
        frames,
        timestamps_ns,
        trigger_ns=trigger_ns,
        range_evidence=None,
        geometry=geometry,
        ops_ball_speed_mph=None,
        iwr_vertical_deg=None,
        ball_tracker=ball_tracker,
        search=PUTT_SEARCH,
    )


def _angle_delta(first_deg: float, second_deg: float) -> float:
    return (first_deg - second_deg + 180.0) % 360.0 - 180.0


def select_camera_assisted_horizontal(
    estimate: CameraBallEstimate,
    *,
    iwr_horizontal_deg: float | None,
    iwr_confidence: float | None,
) -> HorizontalFusionDecision:
    """Select accepted camera output while keeping IWR as an honest fallback."""
    camera_deg = estimate.horizontal_deg
    delta = (
        _angle_delta(camera_deg, iwr_horizontal_deg)
        if camera_deg is not None and iwr_horizontal_deg is not None
        else None
    )
    if estimate.depth_source == "camera_size" and camera_deg is not None:
        return HorizontalFusionDecision(
            camera_deg,
            "camera_only_experimental",
            0.30,
            "camera_only_experimental",
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    if estimate.confidence_tier == "high" and camera_deg is not None:
        return HorizontalFusionDecision(
            camera_deg,
            "camera_assisted_experimental",
            0.75,
            "camera_assisted_high",
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    if estimate.confidence_tier == "experimental" and camera_deg is not None:
        agreement = delta is not None and abs(delta) <= 3.0
        return HorizontalFusionDecision(
            camera_deg,
            "camera_assisted_experimental",
            0.45 if agreement else 0.30,
            (
                "camera_assisted_experimental_agreement"
                if agreement
                else "camera_experimental_disagreement"
                if iwr_horizontal_deg is not None
                else "camera_experimental_no_iwr"
            ),
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    if iwr_horizontal_deg is not None and abs(iwr_horizontal_deg) > MAX_IWR_FALLBACK_ABS_DEG:
        return HorizontalFusionDecision(
            None,
            None,
            None,
            "camera_withheld_iwr_implausible",
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    return HorizontalFusionDecision(
        iwr_horizontal_deg,
        "radar" if iwr_horizontal_deg is not None else None,
        iwr_confidence if iwr_horizontal_deg is not None else None,
        "camera_withheld_fallback_iwr",
        iwr_horizontal_deg,
        camera_deg,
        delta,
    )


__all__ = [
    "FLIGHT_SEARCH",
    "PUTT_SEARCH",
    "BallCandidate",
    "BallSearchProfile",
    "CameraBallEstimate",
    "CameraBallGeometry",
    "HorizontalFusionDecision",
    "estimate_camera_ball_flight",
    "estimate_camera_putt",
    "select_camera_assisted_horizontal",
]
