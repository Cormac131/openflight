"""Pass/fail logic for the closed-loop auto-gain hardware script."""

import argparse
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight.sensitivity.autogain import AT_LIMIT, HOLD, LOWER, RAISE, WAITING
from openflight.sensitivity.envelope import EnvelopePeak

# Loaded by path: tests/test_autogain.py would otherwise shadow the script.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hardware-test" / "test_autogain.py"
_SPEC = importlib.util.spec_from_file_location("autogain_hardware_script", _SCRIPT)
script = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = script
_SPEC.loader.exec_module(script)


def peak(fraction, clipped=False):
    return EnvelopePeak(
        volts=fraction * 3.3,
        timestamp=0.0,
        sample_count=8,
        fraction_of_full_scale=fraction,
        clipped=clipped,
    )


def round_record(position, next_position=None, action=HOLD, fraction=0.7, reason="ok"):
    if next_position is None:
        next_position = position
    return script.RoundRecord(
        position=position,
        next_position=next_position,
        action=action,
        fraction=fraction,
        reason=reason,
    )


class FakePot:
    def __init__(self, position=0):
        self.position = position

    def set_position(self, position, store=False):
        del store
        self.position = position


class ScriptedEnvelope:
    def __init__(self, peaks):
        self.peaks = list(peaks)

    def peak_for_impact(self, _timestamp):
        if not self.peaks:
            return None
        return self.peaks.pop(0)


class TestJudgeRun:
    def test_an_empty_run_fails(self):
        ok, explanation = script.judge_run([])

        assert ok is False
        assert "No rounds" in explanation

    def test_no_envelope_samples_fails(self):
        records = [round_record(0, action=script.NO_SAMPLE, fraction=None)]

        ok, explanation = script.judge_run(records)

        assert ok is False
        assert "never returned envelope samples" in explanation

    def test_a_dead_envelope_fails(self):
        records = [round_record(0, action=WAITING, fraction=0.01) for _ in range(5)]

        ok, explanation = script.judge_run(records)

        assert ok is False
        assert "near zero" in explanation

    def test_holding_inside_the_band_without_moving_is_not_a_pass(self):
        records = [round_record(64, action=HOLD, fraction=0.72) for _ in range(5)]

        ok, explanation = script.judge_run(records, target_low=0.68, target_high=0.76)

        assert ok is False
        assert "never had to move" in explanation

    def test_sitting_at_a_limit_without_moving_fails(self):
        records = [
            round_record(
                0,
                action=AT_LIMIT,
                fraction=0.9,
                reason="at the least sensitive setting",
            )
        ]

        ok, explanation = script.judge_run(records)

        assert ok is False
        assert "limit" in explanation.lower()

    def test_a_raise_that_moves_the_wiper_passes(self):
        records = [
            round_record(0, action=WAITING, fraction=0.2),
            round_record(0, action=WAITING, fraction=0.2),
            round_record(0, next_position=10, action=RAISE, fraction=0.2),
            round_record(10, action=HOLD, fraction=0.7),
        ]

        ok, explanation = script.judge_run(records, target_low=0.68, target_high=0.76)

        assert ok is True
        assert "step 0" in explanation
        assert "10" in explanation

    def test_a_lower_that_moves_the_wiper_passes(self):
        records = [
            round_record(80, next_position=70, action=LOWER, fraction=0.95),
            round_record(70, action=HOLD, fraction=0.72),
        ]

        ok, explanation = script.judge_run(records)

        assert ok is True
        assert "70" in explanation

    def test_a_raise_whose_envelope_does_not_follow_fails(self):
        records = [
            round_record(0, next_position=20, action=RAISE, fraction=0.2),
            round_record(20, action=WAITING, fraction=0.2),
            round_record(20, action=HOLD, fraction=0.19),
        ]

        ok, explanation = script.judge_run(records)

        assert ok is False
        assert "did not" in explanation


class TestEnvelopeResponds:
    def test_a_louder_wiper_must_read_louder(self):
        assert script.envelope_responds(0.10, 0.40) is True

    def test_a_flat_envelope_is_rejected(self):
        ok, explanation = script.envelope_responds_with_reason(0.20, 0.20)

        assert ok is False
        assert "does not track" in explanation

    def test_two_quiet_ends_are_rejected(self):
        ok, explanation = script.envelope_responds_with_reason(0.04, 0.03)

        assert ok is False
        assert "near zero" in explanation
        assert "ADS1115" in explanation
        assert "0.13 V" in explanation or "0.13V" in explanation.replace(" ", "")

    def test_an_idle_reading_is_not_described_as_a_missing_adc(self):
        ok, explanation = script.envelope_responds_with_reason(0.04, 0.03)

        assert ok is False
        assert "not a missing" in explanation.lower()


class TestCollectRounds:
    def test_it_feeds_peaks_into_the_controller_and_moves_the_wiper(self):
        from openflight.sensitivity import AutoGainController

        pot = FakePot(0)
        controller = AutoGainController(min_shots=3, target_low=0.68, target_high=0.76)
        envelope = ScriptedEnvelope([peak(0.15)] * 6)

        records = script.collect_rounds(
            envelope=envelope,
            pot=pot,
            controller=controller,
            shots=6,
            interval_s=0.0,
            sleep=lambda _s: None,
            now=lambda: 1.0,
        )

        assert any(item.action == RAISE for item in records)
        assert pot.position > 0
        assert records[-1].next_position == pot.position

    def test_a_missing_peak_is_recorded_without_moving(self):
        from openflight.sensitivity import AutoGainController

        pot = FakePot(12)
        records = script.collect_rounds(
            envelope=ScriptedEnvelope([]),
            pot=pot,
            controller=AutoGainController(),
            shots=1,
            interval_s=0.0,
            sleep=lambda _s: None,
            now=lambda: 1.0,
        )

        assert records[0].action == script.NO_SAMPLE
        assert pot.position == 12


class TestObserveLevel:
    def test_it_keeps_the_loudest_sample_during_the_dwell(self):
        samples = [peak(0.10), peak(0.40), peak(0.20)]
        clock = {"t": 0.0}

        def now():
            return clock["t"]

        def sleep(seconds):
            clock["t"] += seconds

        def read():
            if samples:
                return samples.pop(0)
            return peak(0.20)

        result = script.observe_level(read=read, dwell_s=0.05, sleep=sleep, now=now, poll_s=0.02)

        assert result is not None
        assert result.fraction_of_full_scale == pytest.approx(0.40)

    def test_no_samples_returns_none(self):
        clock = {"t": 0.0}

        result = script.observe_level(
            read=lambda: None,
            dwell_s=0.04,
            sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
            now=lambda: clock["t"],
            poll_s=0.02,
        )

        assert result is None


class TestHitTracker:
    def test_samples_at_or_below_the_threshold_are_ignored(self):
        tracker = script.HitTracker(threshold_volts=0.05)

        assert tracker.feed(0.02) is None
        assert tracker.feed(0.05) is None

    def test_a_rise_and_fall_reports_the_peak(self):
        tracker = script.HitTracker(threshold_volts=0.05)

        assert tracker.feed(0.02) is None
        assert tracker.feed(0.40, fraction=0.12) is None
        assert tracker.feed(1.80, fraction=0.55) is None
        peak = tracker.feed(0.04, fraction=0.01)

        assert peak is not None
        assert peak.volts == pytest.approx(1.80)
        assert peak.fraction == pytest.approx(0.55)
        assert peak.samples == 2

    def test_two_hits_are_reported_separately(self):
        tracker = script.HitTracker(threshold_volts=0.05)

        tracker.feed(0.8)
        first = tracker.feed(0.01)
        tracker.feed(1.2)
        second = tracker.feed(0.01)

        assert first.volts == pytest.approx(0.8)
        assert second.volts == pytest.approx(1.2)

    def test_a_missing_sample_does_not_end_the_hit(self):
        tracker = script.HitTracker(threshold_volts=0.05)

        tracker.feed(0.9)
        assert tracker.feed(None) is None
        peak = tracker.feed(0.01)

        assert peak is not None
        assert peak.volts == pytest.approx(0.9)

    def test_flush_reports_an_open_hit(self):
        tracker = script.HitTracker(threshold_volts=0.05)
        tracker.feed(0.7)

        peak = tracker.flush()

        assert peak is not None
        assert peak.volts == pytest.approx(0.7)
        assert tracker.flush() is None


class TestValidateArgs:
    def _parser_and_args(self, **overrides):
        defaults = {
            "device": "mcp401x",
            "address": None,
            "envelope_address": 0x48,
            "envelope_channel": 0,
            "start_position": 0,
            "shots": 20,
            "interval": 0.4,
            "settle": 0.3,
            "series_ohms": None,
            "end_to_end_ohms": None,
            "target_low": 0.68,
            "target_high": 0.76,
            "detector_volts": 3.3,
            "watch_seconds": 20.0,
            "threshold_volts": 0.05,
        }
        return argparse.ArgumentParser(), SimpleNamespace(**{**defaults, **overrides})

    def _expect_error(self, **overrides):
        parser, args = self._parser_and_args(**overrides)
        with pytest.raises(SystemExit):
            script.validate_args(parser, args)

    def test_defaults_are_accepted(self):
        parser, args = self._parser_and_args()

        script.validate_args(parser, args)

    def test_an_inverted_target_band_is_refused(self):
        self._expect_error(target_low=0.8, target_high=0.6)

    def test_a_non_positive_shot_count_is_refused(self):
        self._expect_error(shots=0)

    def test_a_negative_interval_is_refused(self):
        self._expect_error(interval=-0.1)

    def test_an_envelope_address_the_ads1115_cannot_use_is_refused(self):
        self._expect_error(envelope_address=0x2F)

    def test_a_non_positive_hit_threshold_is_refused(self):
        self._expect_error(threshold_volts=0)
