"""Noise-floor sweep logic for the X9C104 bring-up script."""

import itertools
import sys
from pathlib import Path

import pytest

# test_x9c104.py is a script -- import it as a module, as test_diagnose.py does.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "hardware-test"))

import test_x9c104 as script  # noqa: E402

from openflight.sensitivity import MAX_POSITION  # noqa: E402


def observation(position, edges=0, high_fraction=0.0):
    return script.TapObservation(position=position, edges=edges, high_fraction=high_fraction)


class TestClassifyWindow:
    def test_no_edges_and_a_low_line_is_quiet(self):
        assert script.classify_window(0, 0.0) == script.QUIET

    def test_any_edge_is_activity(self):
        assert script.classify_window(1, 0.05) == script.ACTIVE

    def test_a_latched_high_gate_is_saturated_not_quiet(self):
        # The failure this check exists for: a stuck-high GATE makes no
        # transitions, so counting edges alone would rank the worst case as the
        # quietest tap in the sweep.
        assert script.classify_window(0, 1.0) == script.SATURATED

    def test_saturation_wins_over_a_few_edges(self):
        assert script.classify_window(3, 0.95) == script.SATURATED

    def test_just_under_the_saturation_threshold_is_still_activity(self):
        assert script.classify_window(4, script.SATURATED_HIGH_FRACTION - 0.01) == script.ACTIVE

    def test_the_observation_exposes_its_verdict(self):
        assert observation(10, edges=2).verdict == script.ACTIVE


class TestRecommendPosition:
    def test_backs_off_from_the_first_noisy_tap(self):
        observations = [
            observation(0),
            observation(10),
            observation(20),
            observation(30, edges=4),
            observation(40, edges=9),
        ]

        target, explanation = script.recommend_position(observations, margin=5)

        assert target == 25
        assert "tap 30" in explanation

    def test_a_saturated_tap_counts_as_the_noise_floor(self):
        observations = [observation(0), observation(20), observation(40, high_fraction=1.0)]

        target, _ = script.recommend_position(observations, margin=5)

        assert target == 35

    def test_the_backed_off_tap_never_falls_below_the_swept_range(self):
        observations = [observation(0), observation(2, edges=1)]

        target, _ = script.recommend_position(observations, margin=20)

        assert target == 0

    def test_an_entirely_quiet_sweep_recommends_the_top_of_the_range(self):
        observations = [observation(tap) for tap in (0, 50, MAX_POSITION)]

        target, explanation = script.recommend_position(observations)

        assert target == MAX_POSITION
        assert "never reached the noise floor" in explanation

    def test_noise_at_the_least_sensitive_tap_refuses_to_recommend(self):
        # Gain cannot be the cause at tap 0, so a number here would send the
        # user tuning a slider when the real fault is in the wiring.
        observations = [observation(0, edges=7), observation(10, edges=9)]

        target, explanation = script.recommend_position(observations)

        assert target is None
        assert "not a gain problem" in explanation

    def test_saturation_at_the_least_sensitive_tap_also_refuses(self):
        observations = [observation(0, high_fraction=1.0), observation(10, edges=2)]

        target, explanation = script.recommend_position(observations)

        assert target is None
        assert "GATE wiring" in explanation

    def test_an_empty_sweep_refuses(self):
        target, explanation = script.recommend_position([])

        assert target is None
        assert "No taps" in explanation

    def test_observations_are_ordered_before_being_read(self):
        # The caller sweeps low to high, but the decision must not depend on it.
        observations = [observation(40, edges=9), observation(0), observation(20, edges=3)]

        target, _ = script.recommend_position(observations, margin=5)

        assert target == 15


class TestSweepPositions:
    def test_both_ends_are_always_visited(self):
        positions = script.sweep_positions(10)

        assert positions[0] == 0
        assert positions[-1] == MAX_POSITION

    def test_a_step_that_divides_the_range_does_not_duplicate_the_end(self):
        positions = script.sweep_positions(11)

        assert positions[-1] == MAX_POSITION
        assert len(positions) == len(set(positions))

    def test_a_step_of_one_visits_every_tap(self):
        assert len(script.sweep_positions(1)) == MAX_POSITION + 1


class TestObserveTap:
    class FakeGate:
        """A GATE line that cycles scripted levels and fires edge callbacks.

        Cycles rather than exhausts: ``observe_tap`` loops on the clock, not on
        a sample budget, so a finite list would silently pad the window with
        zeros and dilute the measured high fraction.
        """

        def __init__(self, levels):
            self.levels = itertools.cycle(levels)
            self.when_activated = None
            self._previous = 0

        @property
        def value(self):
            level = next(self.levels)
            if level and not self._previous and self.when_activated:
                self.when_activated()
            self._previous = level
            return level

    def test_a_quiet_line_reports_no_activity(self, monkeypatch):
        monkeypatch.setattr(script.time, "sleep", lambda _seconds: None)
        gate = self.FakeGate([0])

        result = script.observe_tap(gate, position=12, dwell_s=0.01, sample_hz=10000.0)

        assert result.position == 12
        assert result.high_fraction == 0.0
        assert result.verdict == script.QUIET

    def test_a_line_held_high_reports_saturation(self, monkeypatch):
        monkeypatch.setattr(script.time, "sleep", lambda _seconds: None)
        gate = self.FakeGate([1])

        result = script.observe_tap(gate, position=80, dwell_s=0.01, sample_hz=10000.0)

        assert result.high_fraction == pytest.approx(1.0)
        assert result.verdict == script.SATURATED

    def test_the_callback_is_detached_after_the_window(self, monkeypatch):
        # Left attached, the next tap's window would inherit this one's edges.
        monkeypatch.setattr(script.time, "sleep", lambda _seconds: None)
        gate = self.FakeGate([0])

        script.observe_tap(gate, position=0, dwell_s=0.01, sample_hz=10000.0)

        assert gate.when_activated is None
