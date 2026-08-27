"""Noise-floor sweep and argument logic for the digipot bring-up script."""

import argparse
import itertools
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# test_digipot.py is a script -- import it as a module, as test_diagnose.py does.
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "hardware-test"))

import test_digipot as script  # noqa: E402

from openflight.sensitivity import MAX_POSITION  # noqa: E402


def observation(position, edges=0, high_fraction=0.0):
    return script.StepObservation(position=position, edges=edges, high_fraction=high_fraction)


class TestClassifyWindow:
    def test_no_edges_and_a_low_line_is_quiet(self):
        assert script.classify_window(0, 0.0) == script.QUIET

    def test_any_edge_is_activity(self):
        assert script.classify_window(1, 0.05) == script.ACTIVE

    def test_a_latched_high_gate_is_saturated_not_quiet(self):
        # A stuck-high GATE makes no transitions, so counting edges alone would
        # rank the worst case as the quietest step in the sweep.
        assert script.classify_window(0, 1.0) == script.SATURATED

    def test_saturation_wins_over_a_few_edges(self):
        assert script.classify_window(3, 0.95) == script.SATURATED


class TestRecommendPosition:
    def test_backs_off_from_the_first_noisy_step(self):
        observations = [observation(s) for s in (0, 16, 32)] + [observation(48, edges=4)]

        target, explanation = script.recommend_position(observations, margin=6)

        assert target == 42
        assert "step 48" in explanation

    def test_a_saturated_step_counts_as_the_noise_floor(self):
        observations = [observation(0), observation(40, high_fraction=1.0)]

        assert script.recommend_position(observations, margin=6)[0] == 34

    def test_the_backed_off_step_never_falls_below_the_swept_range(self):
        observations = [observation(0), observation(2, edges=1)]

        assert script.recommend_position(observations, margin=20)[0] == 0

    def test_an_entirely_quiet_sweep_recommends_the_top(self):
        observations = [observation(s) for s in (0, 64, MAX_POSITION)]

        target, explanation = script.recommend_position(observations)

        assert target == MAX_POSITION
        assert "never reached the noise floor" in explanation

    def test_noise_at_the_least_sensitive_step_refuses_to_recommend(self):
        # Gain cannot be the cause at step 0, so a number here would send the
        # user tuning a slider when the real fault is in the wiring.
        target, explanation = script.recommend_position([observation(0, edges=7)])

        assert target is None
        assert "not a gain problem" in explanation

    def test_an_empty_sweep_refuses(self):
        assert script.recommend_position([])[0] is None

    def test_observations_are_ordered_before_being_read(self):
        observations = [observation(48, edges=9), observation(0), observation(32, edges=3)]

        assert script.recommend_position(observations, margin=6)[0] == 26


class TestSweepPositions:
    def test_both_ends_are_always_visited(self):
        positions = script.sweep_positions(8, MAX_POSITION)

        assert positions[0] == 0
        assert positions[-1] == MAX_POSITION

    def test_no_duplicate_end_when_the_step_divides_the_range(self):
        positions = script.sweep_positions(MAX_POSITION, MAX_POSITION)

        assert len(positions) == len(set(positions))

    def test_a_step_of_one_visits_every_step(self):
        assert len(script.sweep_positions(1, MAX_POSITION)) == MAX_POSITION + 1


class TestObserveStep:
    class FakeGate:
        """A GATE line that cycles scripted levels and fires edge callbacks.

        Cycles rather than exhausts: observe_step loops on the clock, not on a
        sample budget, so a finite list would pad the window with zeros.
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
        monkeypatch.setattr(script.time, "sleep", lambda _s: None)

        result = script.observe_step(self.FakeGate([0]), 12, dwell_s=0.01, sample_hz=10000.0)

        assert result.position == 12
        assert result.verdict == script.QUIET

    def test_a_line_held_high_reports_saturation(self, monkeypatch):
        monkeypatch.setattr(script.time, "sleep", lambda _s: None)

        result = script.observe_step(self.FakeGate([1]), 80, dwell_s=0.01, sample_hz=10000.0)

        assert result.high_fraction == pytest.approx(1.0)
        assert result.verdict == script.SATURATED

    def test_the_callback_is_detached_after_the_window(self, monkeypatch):
        # Left attached, the next step's window would inherit this one's edges.
        monkeypatch.setattr(script.time, "sleep", lambda _s: None)
        gate = self.FakeGate([0])

        script.observe_step(gate, 0, dwell_s=0.01, sample_hz=10000.0)

        assert gate.when_activated is None


class TestValidateArgs:
    def _parser_and_args(self, **overrides):
        defaults = {
            "device": "mcp401x",
            "address": None,
            "position": None,
            "sweep_step": 8,
            "sweep_dwell": 2.0,
            "settle": 0.3,
            "margin": 6,
            "series_ohms": 33_000.0,
        }
        return argparse.ArgumentParser(), SimpleNamespace(**{**defaults, **overrides})

    def _expect_error(self, **overrides):
        parser, args = self._parser_and_args(**overrides)
        with pytest.raises(SystemExit):
            script.validate_args(parser, args)

    def test_defaults_are_accepted(self):
        parser, args = self._parser_and_args()

        script.validate_args(parser, args)

    @pytest.mark.parametrize("address", [0x28, 0x2E, 0x48])
    def test_an_address_the_mcp401x_cannot_use_is_refused(self, address):
        # Fixed at 0x2f, so any override would only reach another device.
        self._expect_error(address=address)

    def test_the_mcp401x_fixed_address_is_accepted(self):
        parser, args = self._parser_and_args(address=0x2F)

        script.validate_args(parser, args)

    @pytest.mark.parametrize("address", [0x27, 0x2C])
    def test_an_address_outside_the_ds3502_range_is_refused(self, address):
        self._expect_error(device="ds3502", address=address)

    def test_a_ds3502_jumper_address_is_accepted(self):
        parser, args = self._parser_and_args(device="ds3502", address=0x2A)

        script.validate_args(parser, args)

    @pytest.mark.parametrize("step", [0, -1, MAX_POSITION + 1])
    def test_an_unusable_sweep_step_is_refused(self, step):
        self._expect_error(sweep_step=step)

    @pytest.mark.parametrize("position", [-1, MAX_POSITION + 1])
    def test_an_out_of_range_position_is_refused(self, position):
        self._expect_error(position=position)

    def test_a_negative_dwell_is_refused(self):
        # time.sleep() raises on a negative delay, so this would otherwise
        # abort mid-sweep with a traceback.
        self._expect_error(sweep_dwell=-1.0)

    def test_a_negative_settle_is_refused(self):
        self._expect_error(settle=-0.5)

    def test_a_negative_series_resistor_is_refused(self):
        self._expect_error(series_ohms=-1.0)

    def test_a_negative_margin_is_refused(self):
        self._expect_error(margin=-1)
