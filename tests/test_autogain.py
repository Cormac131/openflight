"""Closed-loop gain control: banding, damping, stability, and end stops."""

import pytest

from openflight.sensitivity import MAX_POSITION
from openflight.sensitivity.autogain import (
    AT_LIMIT,
    HOLD,
    LOWER,
    RAISE,
    WAITING,
    AutoGainController,
    position_for_gain_ratio,
)


def build(**kwargs):
    return AutoGainController(**kwargs)


def feed(controller, fractions, position):
    """Push several shots at one position and return the last decision."""
    decision = None
    for fraction in fractions:
        decision = controller.observe(fraction, position)
    return decision


class TestConstruction:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"target_low": 0.8, "target_high": 0.6},
            {"target_low": 0.0, "target_high": 0.8},
            {"target_low": 0.6, "target_high": 1.0},
            {"min_shots": 0},
            {"min_shots": 5, "history": 3},
            {"max_step": 0},
            {"damping": 0.0},
            {"damping": 1.5},
        ],
    )
    def test_nonsense_settings_are_refused(self, kwargs):
        with pytest.raises(ValueError):
            build(**kwargs)

    def test_the_default_band_brackets_the_documented_target(self):
        controller = build()

        assert controller.target_low == 0.60
        assert controller.target_high == 0.80
        assert 0.60 < controller.target_centre < 0.80


class TestWarmUp:
    def test_it_waits_for_enough_shots_before_moving(self):
        controller = build(min_shots=3)

        decision = feed(controller, [0.2, 0.2], 64)

        assert decision.action == WAITING
        assert decision.changed is False
        assert "2/3" in decision.reason

    def test_the_third_shot_is_enough_to_act(self):
        controller = build(min_shots=3)

        decision = feed(controller, [0.2, 0.2, 0.2], 64)

        assert decision.action == RAISE


class TestBanding:
    @pytest.mark.parametrize("fraction", [0.60, 0.70, 0.80])
    def test_a_peak_inside_the_band_holds(self, fraction):
        controller = build()

        decision = feed(controller, [fraction] * 3, 64)

        assert decision.action == HOLD
        assert decision.changed is False

    def test_a_quiet_peak_raises_the_gain(self):
        controller = build()

        decision = feed(controller, [0.25] * 3, 64)

        assert decision.action == RAISE
        assert decision.next_position > 64

    def test_a_loud_peak_lowers_the_gain(self):
        controller = build()

        decision = feed(controller, [0.95] * 3, 64)

        assert decision.action == LOWER
        assert decision.next_position < 64

    def test_the_decision_reports_the_median_it_used(self):
        controller = build()

        decision = feed(controller, [0.2, 0.9, 0.2], 64)

        # Median, not mean: one fat strike must not drag the decision.
        assert decision.median_fraction == pytest.approx(0.2)

    def test_an_outlier_does_not_move_the_gain_on_its_own(self):
        controller = build()

        decision = feed(controller, [0.7, 0.7, 0.05], 64)

        assert decision.action == HOLD


class TestSteppingAndDamping:
    def test_a_correction_is_bounded_by_max_step(self):
        controller = build(max_step=4)

        decision = feed(controller, [0.05] * 3, 64)

        assert abs(decision.next_position - 64) <= 4

    def test_damping_moves_only_part_of_the_way(self):
        gentle = build(damping=0.3, max_step=MAX_POSITION)
        full = build(damping=1.0, max_step=MAX_POSITION)

        gentle_move = abs(feed(gentle, [0.3] * 3, 64).next_position - 64)
        full_move = abs(feed(full, [0.3] * 3, 64).next_position - 64)

        assert gentle_move < full_move

    def test_a_correction_worth_making_never_rounds_to_a_stall(self):
        # A tiny modelled step damped toward zero must still move one step,
        # or the loop parks just outside the band forever.
        controller = build(damping=0.01, max_step=MAX_POSITION, target_low=0.60, target_high=0.61)

        decision = feed(controller, [0.55] * 3, 64)

        assert decision.changed is True

    def test_history_is_cleared_after_a_move(self):
        # Peaks measured at the old gain say nothing about the new one.
        controller = build(min_shots=3)
        moved = feed(controller, [0.2] * 3, 64)

        following = controller.observe(0.2, moved.next_position)

        assert following.action == WAITING


class TestEndStops:
    def test_it_reports_being_at_the_bottom_rather_than_looping(self):
        controller = build()

        decision = feed(controller, [0.99] * 3, 0)

        assert decision.action == AT_LIMIT
        assert decision.changed is False
        assert "series resistor" in decision.reason

    def test_it_reports_being_at_the_top(self):
        controller = build()

        decision = feed(controller, [0.05] * 3, MAX_POSITION)

        assert decision.action == AT_LIMIT
        assert decision.changed is False

    def test_a_move_never_leaves_the_wiper_range(self):
        controller = build(max_step=MAX_POSITION, damping=1.0)

        for position in (0, 1, 64, MAX_POSITION - 1, MAX_POSITION):
            controller.reset()
            for fraction in (0.02, 0.99):
                controller.reset()
                decision = feed(controller, [fraction] * 3, position)
                assert 0 <= decision.next_position <= MAX_POSITION


class TestClipping:
    def test_a_clipped_shot_acts_immediately(self):
        # Waiting three shots on a railed preamp means three more ruined
        # captures, and the measured fraction understates by an unknown amount.
        controller = build(min_shots=3)

        decision = controller.observe(1.0, 64, clipped=True)

        assert decision.action == LOWER
        assert decision.next_position < 64

    def test_a_clipped_shot_uses_the_full_step(self):
        controller = build(max_step=10)

        decision = controller.observe(1.0, 64, clipped=True)

        assert decision.next_position == 54

    def test_clipping_at_the_bottom_says_gain_is_not_the_problem(self):
        controller = build()

        decision = controller.observe(1.0, 0, clipped=True)

        assert decision.action == AT_LIMIT
        assert "not the limit" in decision.reason

    def test_a_clipped_shot_discards_earlier_peaks(self):
        controller = build(min_shots=3)
        controller.observe(0.7, 64)
        controller.observe(0.7, 64)

        controller.observe(1.0, 64, clipped=True)
        following = controller.observe(0.7, 54)

        assert following.action == WAITING


class TestEepromCommits:
    def test_nothing_is_committed_while_still_settling(self):
        controller = build(commit_after_stable=3)

        decision = feed(controller, [0.7] * 3, 64)

        assert decision.commit is False

    def test_a_settled_gain_is_committed_once(self):
        controller = build(min_shots=3, history=5, commit_after_stable=3)
        feed(controller, [0.2] * 3, 64)  # forces a move, marking it uncommitted
        decisions = [controller.observe(0.7, 70) for _ in range(8)]

        commits = [d for d in decisions if d.commit]

        # Exactly one write per settling event: at a write per shot the part's
        # EEPROM endurance would not survive a season.
        assert len(commits) == 1

    def test_a_later_move_re_arms_the_commit(self):
        controller = build(min_shots=3, history=5, commit_after_stable=3)
        feed(controller, [0.2] * 3, 64)
        for _ in range(8):
            controller.observe(0.7, 70)

        feed(controller, [0.2] * 3, 70)
        again = [controller.observe(0.7, 80) for _ in range(8)]

        assert any(d.commit for d in again)

    def test_commits_can_be_disabled(self):
        controller = build(min_shots=3, commit_after_stable=0)
        feed(controller, [0.2] * 3, 64)

        decisions = [controller.observe(0.7, 70) for _ in range(20)]

        assert not any(d.commit for d in decisions)


class TestGainModel:
    def test_a_ratio_of_one_is_the_same_step(self):
        assert position_for_gain_ratio(64, 1.0) == 64

    def test_more_gain_means_a_higher_step(self):
        assert position_for_gain_ratio(64, 1.3) > 64

    def test_less_gain_means_a_lower_step(self):
        assert position_for_gain_ratio(64, 0.7) < 64

    def test_the_result_stays_inside_the_wiper_range(self):
        assert position_for_gain_ratio(64, 100.0) == MAX_POSITION
        assert position_for_gain_ratio(64, 0.001) == 0

    def test_a_different_series_resistor_changes_the_answer(self):
        # The series resistor decides where the span sits, so the same ratio
        # lands on a different step.
        assert position_for_gain_ratio(64, 1.05, 27_000.0) != position_for_gain_ratio(
            64, 1.05, 39_000.0
        )

    def test_the_whole_span_buys_only_a_narrow_gain_range(self):
        """Pinned because it bounds what the loop can ever fix.

        R17 works against the board's fixed 100k R3, so a 10k pot moves the
        preamp leg by well under 2x end to end. Auto-gain is a trim inside the
        window the series resistor chooses, not a wide-range AGC -- and the
        controller's at_limit message saying to change that resistor is the
        important output when a setup falls outside it."""
        from openflight.sensitivity import preamp_feedback_ohms

        span = preamp_feedback_ohms(MAX_POSITION) / preamp_feedback_ohms(0)

        assert 1.1 < span < 1.4


class TestConvergence:
    def test_the_loop_settles_inside_the_band(self):
        """A simulated detector whose envelope tracks preamp gain must be
        driven into the band and stay there, not oscillate around it."""
        from openflight.sensitivity import preamp_feedback_ohms

        controller = build()
        position = 0
        # Chosen so the in-band answer is somewhere in the middle of travel.
        scale = 0.72 / preamp_feedback_ohms(70)

        actions = []
        for _ in range(60):
            fraction = min(1.0, preamp_feedback_ohms(position) * scale)
            decision = controller.observe(fraction, position, clipped=fraction >= 0.98)
            actions.append(decision.action)
            position = decision.next_position

        final = min(1.0, preamp_feedback_ohms(position) * scale)
        assert controller.target_low <= final <= controller.target_high
        # And once there it stops moving.
        assert actions[-5:] == [HOLD] * 5
