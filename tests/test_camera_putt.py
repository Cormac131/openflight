"""Tests for camera-only putt speed and start line from the ball tracker."""

import math
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

import openflight.camera.ball_flight as ball_flight_module
from openflight.camera.ball_flight import (
    FLIGHT_SEARCH,
    PUTT_SEARCH,
    BallCandidate,
    BallSearchProfile,
    CameraBallGeometry,
    _camera_model,
    _candidates,
    _half_max_diameter_px,
    _path_estimate,
    _pixel_paths,
    _refine_anchor_diameter,
    _rough_path_score,
    _step_pairs,
    estimate_camera_ball_flight,
    estimate_camera_putt,
    select_camera_assisted_horizontal,
)
from openflight.camera.club_motion import BALL_DIAMETER_MM, ReferenceBall

BALL_RADIUS_M = BALL_DIAMETER_MM / 2000.0
MPH_PER_MS = 2.23694
FPS = 300.0
FOCAL_PX = 480.0
IMAGE_WIDTH = 640
IMAGE_HEIGHT = 400


def _putt_geometry(**overrides) -> CameraBallGeometry:
    return CameraBallGeometry.from_camera_measurements(
        camera_height_m=0.20955,
        ball_forward_m=1.5,
        image_width_px=IMAGE_WIDTH,
        image_height_px=IMAGE_HEIGHT,
        **overrides,
    )


def _project_world_point(point: np.ndarray, geometry: CameraBallGeometry) -> tuple[float, float]:
    """Pinhole projection for a level camera looking down the target line."""
    vector = point - geometry.camera_origin
    x_px = IMAGE_WIDTH / 2 + FOCAL_PX * vector[0] / vector[1]
    y_px = IMAGE_HEIGHT / 2 - FOCAL_PX * vector[2] / vector[1]
    if geometry.horizontal_pixel_sign < 0:
        x_px = IMAGE_WIDTH - x_px
    return x_px, y_px


def _apparent_diameter_px(point: np.ndarray, geometry: CameraBallGeometry) -> float:
    return (
        FOCAL_PX * geometry.ball_diameter_m / float(np.linalg.norm(point - geometry.camera_origin))
    )


def _rolling_positions(
    *,
    speed_mph: float,
    start_line_deg: float,
    duration_s: float,
    frame_stride: int,
    geometry: CameraBallGeometry,
) -> tuple[np.ndarray, np.ndarray]:
    speed_ms = speed_mph / MPH_PER_MS
    heading = math.radians(start_line_deg)
    velocity = np.array([speed_ms * math.sin(heading), speed_ms * math.cos(heading), 0.0])
    start = np.array([0.0, geometry.ball_forward_m, geometry.ball_height_m])
    times = np.arange(0.0, duration_s, frame_stride / FPS)
    return times, np.asarray([start + velocity * t for t in times])


def _rolling_candidates(
    *,
    speed_mph: float,
    start_line_deg: float,
    geometry: CameraBallGeometry,
    duration_s: float = 0.4,
    frame_stride: int = PUTT_SEARCH.frame_stride,
) -> tuple[list[BallCandidate], np.ndarray, ReferenceBall]:
    times, positions = _rolling_positions(
        speed_mph=speed_mph,
        start_line_deg=start_line_deg,
        duration_s=duration_s,
        frame_stride=frame_stride,
        geometry=geometry,
    )
    anchor_x, anchor_y = _project_world_point(positions[0], geometry)
    anchor = ReferenceBall(
        x=anchor_x,
        y=anchor_y,
        diameter_px=_apparent_diameter_px(positions[0], geometry),
        area_px=146,
    )
    candidates = []
    for point in positions:
        x_px, y_px = _project_world_point(point, geometry)
        diameter = _apparent_diameter_px(point, geometry)
        candidates.append(
            BallCandidate(
                x=x_px,
                y=y_px,
                area=max(5, round(math.pi * (diameter / 2) ** 2)),
                width=max(2, round(diameter)),
                height=max(2, round(diameter)),
                fill=0.78,
                circularity=0.9,
                mean_intensity=235.0,
            )
        )
    return candidates, np.asarray(times * 1e9, dtype=np.int64), anchor


def _putt_path_estimate(
    candidates, timestamps_ns, anchor, geometry, *, search=PUTT_SEARCH, ops=None
):
    return _path_estimate(
        path=list(enumerate(candidates)),
        frame_indices=list(range(len(candidates))),
        timestamps_ns=timestamps_ns,
        trigger_ns=0,
        range_evidence=None,
        ops_ball_speed_mph=ops,
        iwr_vertical_deg=None,
        model=_camera_model(anchor, geometry),
        geometry=geometry,
        thresholds=(100, 12, 5),
        search=search,
    )


class TestSearchProfile:
    def test_flight_profile_keeps_the_frozen_full_swing_bands(self):
        assert FLIGHT_SEARCH.post_trigger_frames == 15
        assert FLIGHT_SEARCH.frame_stride == 1
        assert FLIGHT_SEARCH.step_dy_band_px == (-38.0, -0.5)
        assert FLIGHT_SEARCH.max_candidate_area_px == 400
        assert FLIGHT_SEARCH.vertical_band_deg == (-5.0, 55.0)
        assert FLIGHT_SEARCH.anchor_exempt_diameters == 0.0

    def test_putt_profile_searches_a_long_flat_window(self):
        assert PUTT_SEARCH.post_trigger_frames / FPS >= 0.35
        assert PUTT_SEARCH.step_dy_band_px[0] < 0.0 < PUTT_SEARCH.step_dy_band_px[1]
        assert PUTT_SEARCH.speed_band_mph == (0.5, 15.0)
        assert PUTT_SEARCH.vertical_prior_deg == 0.0

    def test_speed_band_prefers_ops_ratio_gate(self):
        assert PUTT_SEARCH.speed_band(None) == (0.5, 15.0)
        assert FLIGHT_SEARCH.speed_band(100.0) == (50.0, 150.0)
        assert PUTT_SEARCH.speed_band(6.0) == (3.0, 9.0)

    @pytest.mark.parametrize(
        "field, value",
        [
            ("post_trigger_frames", 3),
            ("frame_stride", 0),
            ("seed_nodes", 0),
            ("step_dy_band_px", (4.0, -12.0)),
            ("speed_band_mph", (15.0, 0.5)),
            ("speed_band_mph", (0.0, 15.0)),
            ("vertical_band_deg", (10.0, -10.0)),
            ("min_step_s", -0.1),
            ("anchor_exempt_diameters", -1.0),
            ("late_start_penalty", -1.0),
        ],
    )
    def test_profile_rejects_inconsistent_bands(self, field, value):
        fields = {**PUTT_SEARCH.__dict__, field: value}
        with pytest.raises(ValueError):
            BallSearchProfile(**fields)


class TestCameraOnlyGeometry:
    def test_measured_distance_round_trips_through_ball_forward(self):
        geometry = _putt_geometry()

        assert geometry.ball_forward_m == pytest.approx(1.5)
        assert geometry.radar_height_m == pytest.approx(geometry.camera_height_m)
        assert geometry.ball_height_m == pytest.approx(BALL_RADIUS_M)
        np.testing.assert_allclose(geometry.camera_origin, [0.0, 0.0, 0.20955])

    def test_overrides_reach_the_dataclass(self):
        geometry = _putt_geometry(
            camera_lateral_offset_m=0.0762,
            horizontal_offset_deg=-0.45,
            roll_correction_deg=2.8,
            horizontal_pixel_sign=-1.0,
            ball_height_m=0.04,
        )

        assert geometry.camera_lateral_offset_m == pytest.approx(0.0762)
        assert geometry.horizontal_offset_deg == pytest.approx(-0.45)
        assert geometry.roll_correction_deg == pytest.approx(2.8)
        assert geometry.horizontal_pixel_sign == -1.0
        assert geometry.ball_height_m == pytest.approx(0.04)
        assert geometry.ball_forward_m == pytest.approx(1.5)

    @pytest.mark.parametrize("field", ["radar_height_m", "tee_range_m"])
    def test_derived_fields_cannot_be_overridden(self, field):
        with pytest.raises(TypeError):
            _putt_geometry(**{field: 1.0})

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"camera_height_m": 0.0, "ball_forward_m": 1.5},
            {"camera_height_m": 0.2, "ball_forward_m": -1.0},
            {"camera_height_m": 0.2, "ball_forward_m": 1.5, "ball_height_m": -0.01},
        ],
    )
    def test_rejects_non_positive_measurements(self, kwargs):
        with pytest.raises(ValueError):
            CameraBallGeometry.from_camera_measurements(**kwargs)

    def test_camera_model_recovers_focal_and_level_pitch_from_measured_setup(self):
        geometry = _putt_geometry()
        _candidates_unused, _timestamps, anchor = _rolling_candidates(
            speed_mph=5.0, start_line_deg=0.0, geometry=geometry
        )

        focal_px, pitch_rad, radar_from_camera = _camera_model(anchor, geometry)

        assert focal_px == pytest.approx(FOCAL_PX, rel=1e-6)
        assert pitch_rad == pytest.approx(0.0, abs=1e-6)
        np.testing.assert_allclose(radar_from_camera, [0.0, 0.0, 0.0], atol=1e-12)


class TestFlatPathSearch:
    @staticmethod
    def _nodes(step_dx: float, step_dy: float, count: int = 6) -> list[list[BallCandidate]]:
        return [
            [
                BallCandidate(
                    x=300.0 + step_dx * index,
                    y=260.0 + step_dy * index,
                    area=140,
                    width=13,
                    height=13,
                    fill=0.78,
                    circularity=0.9,
                    mean_intensity=230.0,
                )
            ]
            for index in range(count)
        ]

    def test_putt_profile_links_flat_and_slightly_rising_motion(self):
        anchor = ReferenceBall(300.0, 260.0, 13.0, 140)
        flat = _pixel_paths(self._nodes(0.4, -0.3), anchor, PUTT_SEARCH)
        drifting_down = _pixel_paths(self._nodes(0.4, 0.8), anchor, PUTT_SEARCH)

        assert flat and len(flat[0]) == 6
        assert drifting_down and len(drifting_down[0]) == 6

    def test_flight_profile_still_requires_rising_motion(self):
        anchor = ReferenceBall(300.0, 260.0, 13.0, 140)

        assert _pixel_paths(self._nodes(0.4, -0.3), anchor, FLIGHT_SEARCH) == []
        assert _pixel_paths(self._nodes(0.4, -0.3), anchor) == []
        assert _pixel_paths(self._nodes(2.0, -6.0), anchor, FLIGHT_SEARCH)

    def test_putt_profile_rejects_a_plunging_step(self):
        anchor = ReferenceBall(300.0, 260.0, 13.0, 140)
        assert _pixel_paths(self._nodes(0.4, 6.0), anchor, PUTT_SEARCH) == []

    def test_late_start_penalty_keeps_early_chains_in_the_beam(self):
        """Perspective shrinks the image step downrange, so equal-length chains that
        start late look straighter and would otherwise crowd the early ones out."""
        anchor = ReferenceBall(300.0, 260.0, 13.0, 140)
        nodes = [
            [
                BallCandidate(
                    x=300.0 + 0.4 * index,
                    y=260.0 - 10.0 * (1.0 - 0.97**index),
                    area=140,
                    width=13,
                    height=13,
                    fill=0.78,
                    circularity=0.9,
                    mean_intensity=230.0,
                )
            ]
            for index in range(40)
        ]

        penalised = _pixel_paths(nodes, anchor, PUTT_SEARCH)
        unpenalised = _pixel_paths(nodes, anchor, replace(PUTT_SEARCH, late_start_penalty=0.0))

        assert penalised[0][0][0] == 0
        assert penalised[0][-1][0] == 39
        assert len(penalised[0]) >= 20
        assert unpenalised[0][0][0] > 0

    def test_rough_path_score_penalises_late_starts_only_when_asked(self):
        late = [
            (7, BallCandidate(10, 20, 10, 3, 4, 0.7, 0.8, 220)),
            (8, BallCandidate(12, 17, 10, 3, 4, 0.7, 0.8, 220)),
            (9, BallCandidate(14, 14, 10, 3, 4, 0.7, 0.8, 220)),
        ]

        assert _rough_path_score(late) == pytest.approx(60.0)
        assert _rough_path_score(late, 5.0) == pytest.approx(25.0)
        assert _rough_path_score(late[:2], 5.0) == pytest.approx(5.0)

    def test_putt_profile_seeds_from_a_late_first_detection(self):
        anchor = ReferenceBall(300.0, 260.0, 13.0, 140)
        nodes = self._nodes(0.4, -0.3, count=16)
        for index in range(8):
            nodes[index] = []

        assert _pixel_paths(nodes, anchor, PUTT_SEARCH)
        assert _pixel_paths(nodes, anchor, FLIGHT_SEARCH) == []


class TestStepPairs:
    def test_zero_spacing_uses_consecutive_points(self):
        times = np.array([0.0, 0.01, 0.02, 0.03])
        assert _step_pairs(times, 0.0) == [(0, 1), (1, 2), (2, 3)]

    def test_spacing_builds_non_overlapping_spans(self):
        times = np.arange(17) * 0.125
        pairs = _step_pairs(times, 0.5)

        assert pairs == [(0, 4), (4, 8), (8, 12), (12, 16)]
        assert all(times[second] - times[first] >= 0.5 for first, second in pairs)

    def test_short_path_falls_back_to_a_single_span(self):
        times = np.array([0.0, 0.01, 0.02, 0.03])
        assert _step_pairs(times, 0.1) == [(0, 3)]


class TestPuttPathEstimate:
    @pytest.mark.parametrize("speed_mph, start_line_deg", [(4.0, 0.0), (6.0, 3.0), (10.0, -5.0)])
    def test_recovers_speed_and_start_line_without_ops_speed(self, speed_mph, start_line_deg):
        geometry = _putt_geometry()
        candidates, timestamps, anchor = _rolling_candidates(
            speed_mph=speed_mph, start_line_deg=start_line_deg, geometry=geometry
        )

        result = _putt_path_estimate(candidates, timestamps, anchor, geometry)

        assert result is not None
        _score, estimate = result
        assert estimate.speed_mph == pytest.approx(speed_mph, rel=0.05)
        assert estimate.horizontal_deg == pytest.approx(start_line_deg, abs=0.5)
        assert estimate.vertical_deg == pytest.approx(0.0, abs=1.0)
        assert estimate.speed_error_mph is None

    def test_mirrored_capture_keeps_the_physical_start_line_sign(self):
        geometry = _putt_geometry(horizontal_pixel_sign=-1.0)
        candidates, timestamps, anchor = _rolling_candidates(
            speed_mph=6.0, start_line_deg=3.0, geometry=geometry
        )

        result = _putt_path_estimate(candidates, timestamps, anchor, geometry)

        assert result is not None
        assert result[1].horizontal_deg == pytest.approx(3.0, abs=0.5)

    def test_setup_yaw_correction_applies_to_the_start_line(self):
        geometry = _putt_geometry(horizontal_offset_deg=-1.0)
        candidates, timestamps, anchor = _rolling_candidates(
            speed_mph=6.0, start_line_deg=3.0, geometry=geometry
        )

        result = _putt_path_estimate(candidates, timestamps, anchor, geometry)

        assert result is not None
        assert result[1].horizontal_deg == pytest.approx(2.0, abs=0.5)

    def test_ops_speed_still_gates_when_supplied(self):
        geometry = _putt_geometry()
        candidates, timestamps, anchor = _rolling_candidates(
            speed_mph=6.0, start_line_deg=0.0, geometry=geometry
        )

        matched = _putt_path_estimate(candidates, timestamps, anchor, geometry, ops=6.5)
        mismatched = _putt_path_estimate(candidates, timestamps, anchor, geometry, ops=20.0)

        assert matched is not None
        assert matched[1].speed_error_mph == pytest.approx(-0.5, abs=0.4)
        assert mismatched is None

    def test_flight_profile_rejects_a_rolling_ball(self):
        geometry = _putt_geometry()
        candidates, timestamps, anchor = _rolling_candidates(
            speed_mph=6.0, start_line_deg=0.0, geometry=geometry
        )

        assert (
            _putt_path_estimate(candidates, timestamps, anchor, geometry, search=FLIGHT_SEARCH)
            is None
        )

    @pytest.mark.parametrize("speed_mph", [0.2, 18.0])
    def test_putt_band_rejects_speeds_outside_one_half_to_fifteen_mph(self, speed_mph):
        geometry = _putt_geometry()
        candidates, timestamps, anchor = _rolling_candidates(
            speed_mph=speed_mph, start_line_deg=0.0, geometry=geometry
        )

        assert _putt_path_estimate(candidates, timestamps, anchor, geometry) is None

    def test_stationary_ball_is_not_a_putt(self):
        geometry = _putt_geometry()
        candidates, timestamps, anchor = _rolling_candidates(
            speed_mph=6.0, start_line_deg=0.0, geometry=geometry
        )
        stationary = [candidates[0]] * len(candidates)

        assert _putt_path_estimate(stationary, timestamps, anchor, geometry) is None

    def test_hopping_ball_outside_the_flat_band_is_rejected(self):
        geometry = _putt_geometry()
        candidates, timestamps, anchor = _rolling_candidates(
            speed_mph=6.0, start_line_deg=0.0, geometry=geometry
        )
        climbing = [
            BallCandidate(
                x=candidate.x,
                y=candidate.y - 1.2 * index,
                area=candidate.area,
                width=candidate.width,
                height=candidate.height,
                fill=candidate.fill,
                circularity=candidate.circularity,
                mean_intensity=candidate.mean_intensity,
            )
            for index, candidate in enumerate(candidates)
        ]

        assert _putt_path_estimate(climbing, timestamps, anchor, geometry) is None


class TestAnchorExemptCandidates:
    @staticmethod
    def _scene(ball_shift_px: int) -> tuple[np.ndarray, np.ndarray, ReferenceBall]:
        cv2 = pytest.importorskip("cv2")
        background = np.full(
            (
                120,
                160,
            ),
            40,
            dtype=np.uint8,
        )
        cv2.circle(background, (80, 70), 7, 240, -1)
        frame = np.full((120, 160), 40, dtype=np.uint8)
        cv2.circle(frame, (80 + ball_shift_px, 70), 7, 240, -1)
        return frame, background, ReferenceBall(80.0, 70.0, 14.0, 154)

    def test_background_subtraction_alone_sees_only_a_sliver_of_a_slow_ball(self):
        frame, background, anchor = self._scene(ball_shift_px=2)

        found = _candidates(
            frame, background, anchor, bright_threshold=100, difference_threshold=12, min_area=5
        )

        assert all(candidate.area < 60 for candidate in found)

    def test_anchor_exemption_sees_the_whole_ball_near_the_reference(self):
        frame, background, anchor = self._scene(ball_shift_px=2)

        found = _candidates(
            frame,
            background,
            anchor,
            bright_threshold=100,
            difference_threshold=12,
            min_area=5,
            anchor_exempt_radius_px=1.5 * anchor.diameter_px,
        )

        assert len(found) == 1
        assert found[0].area == pytest.approx(math.pi * 7**2, rel=0.1)
        assert found[0].x == pytest.approx(82.0, abs=0.5)

    def test_exemption_does_not_admit_static_brightness_away_from_the_anchor(self):
        cv2 = pytest.importorskip("cv2")
        frame, background, anchor = self._scene(ball_shift_px=2)
        cv2.circle(background, (130, 70), 6, 240, -1)
        cv2.circle(frame, (130, 70), 6, 240, -1)

        found = _candidates(
            frame,
            background,
            anchor,
            bright_threshold=100,
            difference_threshold=12,
            min_area=5,
            anchor_exempt_radius_px=1.5 * anchor.diameter_px,
        )

        assert [round(candidate.x) for candidate in found] == [82]

    def test_max_area_admits_a_large_near_ball_only_when_raised(self):
        cv2 = pytest.importorskip("cv2")
        background = np.full((120, 160), 40, dtype=np.uint8)
        frame = background.copy()
        cv2.circle(frame, (80, 70), 13, 240, -1)
        anchor = ReferenceBall(70.0, 70.0, 26.0, 530)

        default = _candidates(
            frame, background, anchor, bright_threshold=100, difference_threshold=12, min_area=5
        )
        raised = _candidates(
            frame,
            background,
            anchor,
            bright_threshold=100,
            difference_threshold=12,
            min_area=5,
            max_area=PUTT_SEARCH.max_candidate_area_px,
        )

        assert default == []
        assert len(raised) == 1


class TestHalfMaximumDiameter:
    @staticmethod
    def _disk(radius: float, *, level: int = 240, floor: int = 40) -> tuple[np.ndarray, np.ndarray]:
        cv2 = pytest.importorskip("cv2")
        frame = np.full((60, 60), floor, dtype=np.uint8)
        shift = 4
        cv2.circle(
            frame,
            (round(30.3 * 2**shift), round(29.6 * 2**shift)),
            round((radius - 0.5) * 2**shift),
            level,
            -1,
            lineType=cv2.LINE_AA,
            shift=shift,
        )
        return frame, frame > 115

    @pytest.mark.parametrize("radius", [4.0, 6.8, 10.5])
    def test_half_maximum_diameter_is_threshold_independent(self, radius):
        frame, _member = self._disk(radius)
        at_low = _half_max_diameter_px(frame, frame > 100)
        at_high = _half_max_diameter_px(frame, frame > 200)

        area_diameter_low = math.sqrt(4 * np.count_nonzero(frame > 100) / math.pi)
        area_diameter_high = math.sqrt(4 * np.count_nonzero(frame > 200) / math.pi)

        assert at_low == pytest.approx(2 * radius, abs=0.35)
        assert at_high == pytest.approx(at_low, abs=0.05)
        assert area_diameter_low - area_diameter_high > 0.5

    def test_low_contrast_component_is_not_refined(self):
        frame, member = self._disk(6.0, level=70)
        assert _half_max_diameter_px(frame, member) is None

    def test_empty_component_is_not_refined(self):
        frame, _member = self._disk(6.0)
        assert _half_max_diameter_px(frame, np.zeros_like(frame, dtype=bool)) is None

    def test_anchor_is_remeasured_at_half_maximum(self):
        frame, _member = self._disk(6.8)
        anchor = ReferenceBall(30.0, 30.0, 20.0, 314)

        refined = _refine_anchor_diameter(
            frame, anchor, bright_threshold=115, search_radius_px=30.0
        )

        assert refined.diameter_px == pytest.approx(13.6, abs=0.35)
        assert (refined.x, refined.y) == (anchor.x, anchor.y)

    def test_anchor_is_kept_when_no_bright_component_is_nearby(self):
        frame = np.full((60, 60), 40, dtype=np.uint8)
        anchor = ReferenceBall(30.0, 30.0, 13.0, 133)

        assert (
            _refine_anchor_diameter(frame, anchor, bright_threshold=115, search_radius_px=20.0)
            is anchor
        )

    def test_anchor_is_kept_when_the_refined_size_is_implausible(self):
        frame, _member = self._disk(6.8)
        anchor = ReferenceBall(30.0, 30.0, 40.0, 1256)

        assert (
            _refine_anchor_diameter(frame, anchor, bright_threshold=115, search_radius_px=30.0)
            is anchor
        )

    def test_candidates_carry_the_refined_diameter_only_when_asked(self):
        frame, _member = self._disk(6.8)
        background = np.full((60, 60), 40, dtype=np.uint8)
        anchor = ReferenceBall(30.0, 30.0, 13.6, 145)

        plain = _candidates(
            frame, background, anchor, bright_threshold=100, difference_threshold=12, min_area=5
        )
        refined = _candidates(
            frame,
            background,
            anchor,
            bright_threshold=100,
            difference_threshold=12,
            min_area=5,
            refine_diameter=True,
        )

        assert plain[0].diameter_px is None
        assert refined[0].diameter_px == pytest.approx(13.6, abs=0.35)


def _render_putt_capture(
    *,
    speed_mph: float,
    start_line_deg: float,
    geometry: CameraBallGeometry,
    pre_frames: int = 20,
    post_frames: int = 130,
    ground_level: int = 40,
):
    cv2 = pytest.importorskip("cv2")
    times, positions = _rolling_positions(
        speed_mph=speed_mph,
        start_line_deg=start_line_deg,
        duration_s=post_frames / FPS,
        frame_stride=1,
        geometry=geometry,
    )
    stationary = [positions[0]] * pre_frames
    frames = []
    shift = 4
    for point in [*stationary, *positions]:
        frame = np.full((IMAGE_HEIGHT, IMAGE_WIDTH), ground_level, dtype=np.uint8)
        x_px, y_px = _project_world_point(point, geometry)
        # cv2 covers pixel centres within the radius, so the drawn disk is about
        # one pixel wider than 2r; shrink the radius to render the true diameter.
        radius = _apparent_diameter_px(point, geometry) / 2 - 0.5
        cv2.circle(
            frame,
            (round(x_px * 2**shift), round(y_px * 2**shift)),
            round(radius * 2**shift),
            240,
            -1,
            lineType=cv2.LINE_AA,
            shift=shift,
        )
        frames.append(frame)
    total = pre_frames + len(positions)
    timestamps_ns = np.arange(total, dtype=np.int64) * int(1e9 / FPS)
    trigger_ns = int(timestamps_ns[pre_frames])
    return np.stack(frames), timestamps_ns, trigger_ns


class TestEstimateCameraPutt:
    def test_recovers_speed_and_start_line_from_rendered_frames(self):
        geometry = _putt_geometry()
        frames, timestamps_ns, trigger_ns = _render_putt_capture(
            speed_mph=6.0, start_line_deg=2.0, geometry=geometry
        )

        estimate = estimate_camera_putt(
            frames, timestamps_ns, trigger_ns=trigger_ns, geometry=geometry
        )

        assert estimate.status == "accepted_camera_only", estimate
        assert estimate.search_profile == "putt"
        assert estimate.depth_source == "camera_size"
        assert estimate.speed_mph == pytest.approx(6.0, rel=0.1)
        assert estimate.horizontal_deg == pytest.approx(2.0, abs=1.0)
        assert estimate.vertical_deg == pytest.approx(0.0, abs=2.0)
        assert estimate.speed_error_mph is None
        assert estimate.confidence_tier == "experimental"
        assert estimate.support >= 2
        assert estimate.first_frame is not None and estimate.first_frame >= 20
        assert estimate.last_frame is not None and estimate.last_frame > 20 + 60

    def test_flight_search_cannot_see_the_same_putt(self):
        geometry = _putt_geometry()
        frames, timestamps_ns, trigger_ns = _render_putt_capture(
            speed_mph=6.0, start_line_deg=2.0, geometry=geometry
        )

        estimate = estimate_camera_ball_flight(
            frames,
            timestamps_ns,
            trigger_ns=trigger_ns,
            range_evidence=None,
            geometry=geometry,
            ops_ball_speed_mph=None,
            search=FLIGHT_SEARCH,
        )

        assert estimate.status == "rejected_no_stable_path"
        assert estimate.search_profile == "flight"

    def test_short_post_trigger_capture_is_reported(self):
        geometry = _putt_geometry()
        frames, timestamps_ns, trigger_ns = _render_putt_capture(
            speed_mph=6.0, start_line_deg=2.0, geometry=geometry, post_frames=8
        )

        estimate = estimate_camera_putt(
            frames, timestamps_ns, trigger_ns=trigger_ns, geometry=geometry
        )

        assert estimate.status == "rejected_insufficient_post_trigger_frames"

    def test_putt_estimate_is_selected_as_camera_only_experimental(self):
        geometry = _putt_geometry()
        frames, timestamps_ns, trigger_ns = _render_putt_capture(
            speed_mph=6.0, start_line_deg=-3.0, geometry=geometry
        )
        estimate = estimate_camera_putt(
            frames, timestamps_ns, trigger_ns=trigger_ns, geometry=geometry
        )

        decision = select_camera_assisted_horizontal(
            estimate, iwr_horizontal_deg=None, iwr_confidence=None
        )

        assert decision.status == "camera_only_experimental"
        assert decision.selected_deg == pytest.approx(-3.0, abs=1.0)
        assert decision.confidence == pytest.approx(0.30)

    def test_established_anchor_is_used_when_detection_fails(self, monkeypatch):
        geometry = _putt_geometry()
        frames, timestamps_ns, trigger_ns = _render_putt_capture(
            speed_mph=6.0, start_line_deg=2.0, geometry=geometry
        )
        _candidates_unused, _timestamps, anchor = _rolling_candidates(
            speed_mph=6.0, start_line_deg=2.0, geometry=geometry
        )
        monkeypatch.setattr(
            ball_flight_module,
            "detect_reference_ball",
            lambda _frames: (_ for _ in ()).throw(ValueError("not found")),
        )
        tracker = SimpleNamespace(fallback=lambda: anchor)

        estimate = estimate_camera_putt(
            frames, timestamps_ns, trigger_ns=trigger_ns, geometry=geometry, ball_tracker=tracker
        )

        assert estimate.status == "accepted_camera_only"
        assert estimate.speed_mph == pytest.approx(6.0, rel=0.1)
