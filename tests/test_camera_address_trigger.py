"""Tests for the down-the-line ball-at-address state machine."""

from dataclasses import replace

import numpy as np
import pytest
from camera_frames import (
    ADDRESS,
    BALL,
    BALL_RADIUS,
    BALL_X,
    BALL_Y,
    EMPTY,
    FRAME_NS,
    OCCLUDED,
    OCCLUDED_EMPTY,
    FrameFactory,
    ScenarioRunner,
    Scene,
)

from openflight.camera.address_trigger import (
    AddressState,
    AddressTriggerConfig,
    BallAddressStateMachine,
    BallCandidate,
    RoiClass,
    best_ncc_in_window,
    normalized_cross_correlation,
)

ADDRESS_SETUP = [(BALL, 10), (ADDRESS, 20), (BALL, 5)]


def make_runner(**overrides) -> ScenarioRunner:
    machine = BallAddressStateMachine(AddressTriggerConfig(**overrides))
    runner = ScenarioRunner(machine)
    runner.lock()
    return runner


# ---------------------------------------------------------------- helpers


class TestNcc:
    def test_identical_patches_score_one(self):
        patch = np.arange(25, dtype=np.float32).reshape(5, 5)
        assert normalized_cross_correlation(patch, patch) == pytest.approx(1.0)

    def test_flat_patch_scores_zero(self):
        flat = np.full((5, 5), 90, dtype=np.uint8)
        textured = np.arange(25, dtype=np.float32).reshape(5, 5)
        assert normalized_cross_correlation(flat, textured) == 0.0
        assert normalized_cross_correlation(textured, flat) == 0.0

    def test_gain_invariant(self):
        patch = np.arange(25, dtype=np.float32).reshape(5, 5)
        assert normalized_cross_correlation(patch * 1.7 + 10, patch) == pytest.approx(1.0)

    def test_window_search_finds_offset(self):
        rng = np.random.default_rng(3)
        window = rng.normal(100, 20, size=(15, 15)).astype(np.float32)
        template = window[6:11, 2:7].copy()
        score, row, col = best_ncc_in_window(window, template)
        assert score == pytest.approx(1.0)
        assert (row, col) == (6, 2)

    def test_window_smaller_than_template(self):
        assert best_ncc_in_window(np.zeros((3, 3)), np.ones((5, 5))) == (0.0, 0, 0)


class TestConfigValidation:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"roi_radius_scale": 0.9},
            {"stable_acquisitions": 0},
            {"absent_ncc": 0.7, "present_ncc": 0.6},
            {"absent_ncc": 0.0},
            {"search_radius_px": -1},
            {"gone_frames": 0},
            {"max_departure_ms": 0},
            {"confirm_timeout_ms": 0},
            {"address_frames": 0},
            {"surround_change_fraction": 0.0},
            {"surround_scale": 1.5},
            {"stall_gap_ms": 0},
            {"max_occlusion_ms": 0},
            {"empty_history": -1},
        ],
    )
    def test_invalid_config_rejected(self, overrides):
        with pytest.raises(ValueError):
            AddressTriggerConfig(**overrides)


# ------------------------------------------------------------ acquisition


class TestAcquisition:
    def test_lock_requires_stable_acquisitions(self):
        machine = BallAddressStateMachine(AddressTriggerConfig(stable_acquisitions=3))
        factory = FrameFactory()
        frame = factory.render(BALL)
        cand = BallCandidate(BALL_X, BALL_Y, BALL_RADIUS)
        assert machine.acquire(frame, 1, cand) == []
        assert machine.acquire(frame, 2, cand) == []
        events = machine.acquire(frame, 3, cand)
        assert [e.kind for e in events] == ["locked"]
        assert machine.state == AddressState.BALL_PRESENT

    def test_moving_candidate_resets_stability(self):
        machine = BallAddressStateMachine(AddressTriggerConfig(stable_acquisitions=2))
        frame = FrameFactory().render(BALL)
        machine.acquire(frame, 1, BallCandidate(BALL_X, BALL_Y, BALL_RADIUS))
        assert machine.acquire(frame, 2, BallCandidate(BALL_X + 5, BALL_Y, BALL_RADIUS)) == []
        assert machine.state == AddressState.IDLE

    def test_tiny_candidate_ignored(self):
        machine = BallAddressStateMachine(AddressTriggerConfig(stable_acquisitions=1))
        frame = FrameFactory().render(BALL)
        assert machine.acquire(frame, 1, BallCandidate(BALL_X, BALL_Y, 1.0)) == []
        assert machine.state == AddressState.IDLE

    def test_empty_history_used_as_mat_reference(self):
        runner = make_runner()
        assert runner.events[-1].detail["empty_reference"] == "history"

    def test_without_history_falls_back_to_mat_ring(self):
        machine = BallAddressStateMachine()
        runner = ScenarioRunner(machine)
        runner.lock(empty_first=0)
        assert runner.events[-1].detail["empty_reference"] == "mat_ring"

    def test_ball_at_frame_edge_rejected(self):
        machine = BallAddressStateMachine(AddressTriggerConfig(stable_acquisitions=1))
        edge = Scene(ball=(5, BALL_Y))
        events = machine.acquire(
            FrameFactory().render(edge), 1, BallCandidate(5, BALL_Y, BALL_RADIUS)
        )
        assert [e.kind for e in events] == ["rejected_edge"]
        assert machine.state == AddressState.IDLE

    def test_acquire_ignored_once_locked(self):
        runner = make_runner()
        frame = runner.factory.render(BALL)
        assert runner.machine.acquire(frame, 5, BallCandidate(10, 10, 6)) == []

    def test_feed_ignored_while_idle(self):
        machine = BallAddressStateMachine()
        assert machine.feed(FrameFactory().render(BALL), 1) == []

    def test_classify_requires_lock(self):
        with pytest.raises(RuntimeError):
            BallAddressStateMachine().classify(FrameFactory().render(BALL))


# ------------------------------------------------------------ classification


class TestClassification:
    def test_scene_classes(self):
        runner = make_runner()
        machine = runner.machine
        render = runner.factory.render
        assert machine.classify(render(BALL)) == RoiClass.PRESENT
        assert machine.classify(render(ADDRESS)) == RoiClass.PRESENT
        assert machine.classify(render(EMPTY)) == RoiClass.EMPTY
        assert machine.classify(render(OCCLUDED)) == RoiClass.OCCLUDED
        assert machine.classify(render(OCCLUDED_EMPTY)) == RoiClass.OCCLUDED

    def test_mat_ring_fallback_classes(self):
        runner = ScenarioRunner(BallAddressStateMachine())
        runner.lock(empty_first=0)
        render = runner.factory.render
        assert runner.machine.classify(render(EMPTY)) == RoiClass.EMPTY
        assert runner.machine.classify(render(OCCLUDED)) == RoiClass.OCCLUDED


# -------------------------------------------------------------- shots


class TestShot:
    def test_full_shot_triggers_once_with_impact_midpoint(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP)
        assert runner.machine.state == AddressState.ADDRESSED
        last_present_ns = runner.now_ns
        runner.run([(OCCLUDED, 1)])  # clubhead at impact
        first_absent_ns = runner.now_ns
        runner.run([(EMPTY, 9)])
        triggers = runner.triggers()
        assert len(triggers) == 1
        trig = triggers[0].trigger
        assert trig.last_present_sensor_ns == last_present_ns
        assert trig.first_absent_sensor_ns == first_absent_ns
        assert trig.impact_sensor_ns == (last_present_ns + first_absent_ns) // 2
        assert trig.impact_uncertainty_ns == FRAME_NS // 2
        assert trig.addressed is True
        assert trig.confirmed_sensor_ns == runner.now_ns
        assert runner.machine.state == AddressState.TRIGGERED

    @pytest.mark.parametrize("gone_frames", [1, 3, 9])
    def test_k_minus_one_frames_do_not_trigger(self, gone_frames):
        runner = make_runner(gone_frames=gone_frames)
        runner.run(ADDRESS_SETUP)
        runner.run([(EMPTY, gone_frames - 1)])
        assert runner.triggers() == []
        runner.run([(EMPTY, 1)])
        assert len(runner.triggers()) == 1

    def test_triggered_ignores_frames_until_rearm(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP + [(EMPTY, 9)])
        runner.run([(BALL, 5), (EMPTY, 20)])
        assert len(runner.triggers()) == 1
        runner.machine.rearm()
        assert runner.machine.state == AddressState.IDLE
        assert runner.machine.needs_acquisition

    def test_new_ball_after_rearm_triggers_again(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP + [(EMPTY, 9)])
        runner.machine.rearm()
        runner.lock()
        runner.run(ADDRESS_SETUP + [(EMPTY, 9)])
        assert len(runner.triggers()) == 2

    def test_mat_ring_fallback_still_triggers(self):
        runner = ScenarioRunner(BallAddressStateMachine())
        runner.lock(empty_first=0)
        runner.run(ADDRESS_SETUP + [(EMPTY, 9)])
        assert len(runner.triggers()) == 1

    def test_debris_after_impact_is_tolerated(self):
        debris = Scene(ball=None, debris=True)
        runner = make_runner()
        runner.run(ADDRESS_SETUP + [(EMPTY, 3), (debris, 2), (EMPTY, 6)])
        assert len(runner.triggers()) == 1

    def test_candidate_expires_when_not_confirmed(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP + [(EMPTY, 3), (OCCLUDED_EMPTY, 40)])
        assert runner.triggers() == []
        assert "candidate_expired" in runner.kinds()
        assert runner.machine.state == AddressState.ADDRESSED

    def test_ball_reappearing_cancels_candidate(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP + [(EMPTY, 3), (BALL, 1)])
        assert "candidate_cancelled" in runner.kinds()
        assert runner.machine.state == AddressState.ADDRESSED
        runner.run([(EMPTY, 5)])
        assert runner.triggers() == []


# ------------------------------------------------------- address requirement


class TestAddressRequirement:
    def test_shot_without_address_rejected_by_default(self):
        runner = make_runner()
        runner.run([(BALL, 10), (EMPTY, 9)])
        assert runner.triggers() == []
        assert "rejected_not_addressed" in runner.kinds()
        assert runner.machine.state == AddressState.IDLE

    def test_shot_without_address_allowed_when_disabled(self):
        runner = make_runner(require_address=False)
        runner.run([(BALL, 10), (EMPTY, 9)])
        assert len(runner.triggers()) == 1
        assert runner.triggers()[0].trigger.addressed is False

    def test_occlusion_counts_as_address(self):
        runner = make_runner(address_frames=5)
        runner.run([(BALL, 3), (OCCLUDED, 5), (BALL, 3)])
        assert runner.machine.addressed
        assert "addressed" in runner.kinds()

    def test_address_needs_enough_frames(self):
        runner = make_runner(address_frames=15)
        runner.run([(BALL, 3), (ADDRESS, 14)])
        assert not runner.machine.addressed
        runner.run([(ADDRESS, 1)])
        assert runner.machine.addressed


# ---------------------------------------------------------- false triggers


class TestFalseTriggerGuards:
    def test_waggle_occlusion_never_triggers(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP + [(OCCLUDED, 30), (BALL, 5), (OCCLUDED, 60), (BALL, 3)])
        assert runner.triggers() == []
        assert runner.machine.state == AddressState.ADDRESSED

    def test_long_soled_occlusion_keeps_lock(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP + [(OCCLUDED, 900)])  # 3 s behind the ball
        assert runner.machine.state == AddressState.ADDRESSED
        runner.run([(BALL, 5), (EMPTY, 9)])
        assert len(runner.triggers()) == 1

    def test_hand_pickup_is_slow_removal(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP + [(OCCLUDED, 20), (EMPTY, 20)])
        assert runner.triggers() == []
        lost = [e for e in runner.events if e.kind == "lost"]
        assert lost and lost[0].detail["cause"] == "removed"
        assert runner.machine.state == AddressState.IDLE

    def test_departure_window_boundary(self):
        # 6 occluded frames = 20 ms after last present: still a departure.
        runner = make_runner(max_departure_ms=20.0 + 0.5)
        runner.run(ADDRESS_SETUP + [(OCCLUDED_EMPTY, 5), (EMPTY, 9)])
        assert len(runner.triggers()) == 1
        runner = make_runner(max_departure_ms=20.0 - 0.5)
        runner.run(ADDRESS_SETUP + [(OCCLUDED_EMPTY, 6), (EMPTY, 9)])
        assert runner.triggers() == []

    def test_occlusion_timeout_drops_lock(self):
        runner = make_runner(max_occlusion_ms=100.0)
        runner.run(ADDRESS_SETUP + [(OCCLUDED, 40)])
        lost = [e for e in runner.events if e.kind == "lost"]
        assert lost and lost[0].detail["cause"] == "occlusion_timeout"
        assert runner.machine.state == AddressState.IDLE

    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
    def test_noisy_static_ball_never_triggers(self, seed):
        machine = BallAddressStateMachine()
        runner = ScenarioRunner(machine, FrameFactory(seed=seed, noise=8.0))
        runner.lock()
        runner.run([(BALL, 300), (ADDRESS, 300)])
        assert runner.triggers() == []
        assert "candidate_gone" not in runner.kinds()


# --------------------------------------------------------------- nudges


class TestNudge:
    @pytest.mark.parametrize("offset", [1, 3, 6])
    def test_nudge_within_search_radius_relocks(self, offset):
        runner = make_runner(search_radius_px=6)
        nudged = Scene(ball=(BALL_X + offset, BALL_Y))
        runner.run(ADDRESS_SETUP + [(nudged, 3)])
        # Small nudges still match in place; larger ones re-lock. Either way
        # the lock stays within the NCC tolerance of the true position.
        assert abs(runner.machine.locked_ball.x - (BALL_X + offset)) <= 3
        assert runner.triggers() == []
        runner.run([(EMPTY, 9)])
        assert len(runner.triggers()) == 1

    def test_fast_jump_beyond_search_radius_is_a_departure(self):
        # A ball that leaves the ROI + search window within one frame moved
        # like a struck ball (e.g. kicked). The camera cannot tell; the radar's
        # outbound-speed check downstream rejects non-shots.
        runner = make_runner(search_radius_px=6)
        moved = Scene(ball=(BALL_X + 15, BALL_Y))
        runner.run(ADDRESS_SETUP + [(moved, 9)])
        assert runner.machine.locked_ball.x == BALL_X
        assert len(runner.triggers()) == 1

    def test_slow_roll_is_tracked(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP)
        for step in range(1, 21):
            runner.run([(Scene(ball=(BALL_X + step, BALL_Y)), 1)])
        assert runner.machine.locked_ball.x == BALL_X + 20
        assert runner.triggers() == []
        runner.run([(EMPTY, 9)])
        assert len(runner.triggers()) == 1

    def test_large_nudge_drops_history_reference(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP + [(Scene(ball=(BALL_X + 4, BALL_Y)), 1)])
        # Moved more than radius/4: must fall back to mat-ring stats yet still work.
        assert runner.machine._lock.empty_template is None  # pylint: disable=protected-access
        runner.run([(EMPTY, 9)])
        assert len(runner.triggers()) == 1


# ------------------------------------------------------------- exposure


class TestExposure:
    def test_unreported_brightness_step_never_triggers(self):
        runner = make_runner()
        brighter = replace(BALL, gain=1.3)
        runner.run(ADDRESS_SETUP + [(brighter, 100)])
        assert runner.triggers() == []

    def test_reported_brightness_change_is_normalized(self):
        runner = make_runner()
        # Controls changed (and reported): mat is 30% brighter but still mat.
        runner.run(
            [
                (replace(BALL, gain=1.3), 10),
                (replace(ADDRESS, gain=1.3), 20),
                (replace(BALL, gain=1.3), 5),
                (replace(EMPTY, gain=1.3), 9),
            ]
        )
        assert len(runner.triggers()) == 1


# --------------------------------------------------------------- timing


class TestTiming:
    def test_stall_cancels_pending_candidate(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP + [(EMPTY, 3)])
        runner.gap(200 * 1_000_000)
        runner.run([(EMPTY, 20)])
        assert runner.triggers() == []
        assert "stall" in runner.kinds()

    def test_stall_before_departure_blocks_trigger(self):
        runner = make_runner()
        runner.run(ADDRESS_SETUP)
        runner.gap(200 * 1_000_000)
        runner.run([(EMPTY, 20)])
        assert runner.triggers() == []
        assert runner.machine.state == AddressState.IDLE

    def test_dropped_frames_widen_uncertainty(self):
        runner = make_runner(max_departure_ms=25.0)
        runner.run(ADDRESS_SETUP)
        last_present = runner.now_ns
        runner.gap(4 * FRAME_NS)  # dropped frames under the stall limit
        runner.run([(EMPTY, 9)])
        trig = runner.triggers()[0].trigger
        assert trig.first_absent_sensor_ns - last_present == 5 * FRAME_NS
        assert trig.impact_uncertainty_ns == (5 * FRAME_NS) // 2
        assert runner.triggers()[0].detail["impact_uncertainty_ms"] == pytest.approx(
            5 * FRAME_NS / 2 / 1e6
        )
