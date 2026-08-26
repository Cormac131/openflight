"""Noise-floor sweep logic for the X9C104 bring-up script."""

import argparse
import itertools
import sys
from pathlib import Path
from types import SimpleNamespace

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


class TestValidateArgs:
    """Bad values must fail at the CLI, not partway through a sweep with the
    wiper parked somewhere the user did not choose."""

    def _parser_and_args(self, **overrides):
        defaults = {
            "position": None,
            "sweep_step": 10,
            "sweep_dwell": 2.0,
            "settle": 0.3,
            "margin": 5,
            "noise_floor": False,
            "trigger_pin": 17,
            "cs_pin": 22,
            "inc_pin": 23,
            "ud_pin": 24,
        }
        parser = argparse.ArgumentParser()
        return parser, SimpleNamespace(**{**defaults, **overrides})

    def _expect_error(self, **overrides):
        parser, args = self._parser_and_args(**overrides)
        with pytest.raises(SystemExit):
            script.validate_args(parser, args)

    def test_defaults_are_accepted(self):
        parser, args = self._parser_and_args()

        script.validate_args(parser, args)

    def test_a_single_tap_step_is_accepted(self):
        parser, args = self._parser_and_args(sweep_step=1)

        script.validate_args(parser, args)

    def test_a_long_dwell_is_accepted(self):
        parser, args = self._parser_and_args(sweep_dwell=30.0)

        script.validate_args(parser, args)

    @pytest.mark.parametrize("step", [0, -1, MAX_POSITION + 1])
    def test_an_unusable_step_is_refused(self, step):
        self._expect_error(sweep_step=step)

    def test_a_negative_dwell_is_refused(self):
        # time.sleep() raises on a negative delay, so this would otherwise
        # abort mid-sweep with a traceback.
        self._expect_error(sweep_dwell=-1.0)

    def test_a_negative_settle_is_refused(self):
        self._expect_error(settle=-0.5)

    def test_a_negative_margin_is_refused(self):
        self._expect_error(margin=-1)

    @pytest.mark.parametrize("position", [-1, MAX_POSITION + 1])
    def test_an_out_of_range_position_is_refused(self, position):
        self._expect_error(position=position)

    def test_the_trigger_pin_cannot_be_a_digipot_line(self):
        self._expect_error(noise_floor=True, trigger_pin=23)

    def test_that_clash_only_matters_for_the_noise_floor_sweep(self):
        # Without --noise-floor the trigger line is never claimed.
        parser, args = self._parser_and_args(noise_floor=False, trigger_pin=23)

        script.validate_args(parser, args)


class TestHoldAfterParking:
    """A parked wiper only lasts while something drives the lines, so the script
    has to either stay alive or say plainly that the value will not survive."""

    def test_hold_waits_for_the_user_before_releasing(self, monkeypatch, capsys):
        waited = []
        monkeypatch.setattr("builtins.input", lambda *_a: waited.append(True) or "")

        script.hold_after_parking(True)

        assert waited == [True]
        assert "stays put" in capsys.readouterr().out

    def test_hold_survives_a_closed_stdin(self, monkeypatch):
        def raise_eof(*_args):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)

        script.hold_after_parking(True)

    def test_hold_survives_a_ctrl_c(self, monkeypatch):
        def raise_interrupt(*_args):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", raise_interrupt)

        script.hold_after_parking(True)

    def test_without_hold_the_user_is_warned_and_nothing_blocks(self, monkeypatch, capsys):
        def fail(*_args):
            raise AssertionError("must not block without --hold")

        monkeypatch.setattr("builtins.input", fail)

        script.hold_after_parking(False)

        out = capsys.readouterr().out
        assert "will NOT survive" in out
        assert "--hold" in out

    def test_both_paths_explain_that_nvm_was_untouched(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda *_a: "")

        script.hold_after_parking(True)
        held = capsys.readouterr().out
        script.hold_after_parking(False)
        unheld = capsys.readouterr().out

        assert "non-volatile memory" in held
        assert "non-volatile memory" in unheld
