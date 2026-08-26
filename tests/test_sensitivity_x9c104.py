"""X9C104 three-wire protocol, resistance maths, and mock parity."""

import pytest

from openflight.sensitivity import x9c104
from openflight.sensitivity.x9c104 import (
    MAX_POSITION,
    TAP_COUNT,
    X9C104,
    MockX9C104,
    position_for_resistance,
    preamp_feedback_ohms,
    resistance_ohms,
    sensitivity_percent,
)


class RecordingLine:
    """One GPIO line that appends ``(name, level)`` to a shared trace."""

    def __init__(self, name, trace):
        self.name = name
        self.trace = trace
        self.closed = False
        # gpiozero is constructed with initial_value=True, so every line starts
        # high; the trace records that so tests can assert the idle state.
        self.trace.append((name, 1))

    def on(self):
        self.trace.append((self.name, 1))

    def off(self):
        self.trace.append((self.name, 0))

    def close(self):
        self.closed = True


def build_pot(**kwargs):
    """Return a driver wired to recording lines, plus the shared trace."""
    trace = []
    lines = {}
    names = {7: "CS", 8: "INC", 9: "UD"}

    def factory(pin):
        line = RecordingLine(names[pin], trace)
        lines[names[pin]] = line
        return line

    pot = X9C104(
        cs_pin=7,
        inc_pin=8,
        ud_pin=9,
        output_factory=factory,
        sleep=lambda _seconds: None,
        **kwargs,
    )
    return pot, trace, lines


def falling_edges(trace):
    """Count INC high->low transitions that happen while CS is low."""
    edges = 0
    cs_low = False
    inc_high = True
    for name, level in trace:
        if name == "CS":
            cs_low = level == 0
        elif name == "INC":
            if cs_low and inc_high and level == 0:
                edges += 1
            inc_high = level == 1
    return edges


class TestProtocol:
    def test_open_idles_every_line_high(self):
        pot, trace, _ = build_pot()

        pot.open()

        assert trace == [("CS", 1), ("INC", 1), ("UD", 1)]

    def test_open_is_idempotent(self):
        pot, trace, _ = build_pot()

        pot.open()
        pot.open()

        assert trace.count(("CS", 1)) == 1

    def test_open_releases_partial_lines_when_a_later_line_fails(self):
        opened = []

        def factory(pin):
            if pin == 9:
                raise OSError("GPIO 9 busy")
            line = RecordingLine(str(pin), [])
            opened.append(line)
            return line

        pot = X9C104(cs_pin=7, inc_pin=8, ud_pin=9, output_factory=factory, sleep=lambda _s: None)

        with pytest.raises(RuntimeError, match="Could not claim X9C104 GPIO lines"):
            pot.open()

        assert [line.closed for line in opened] == [True, True]
        assert pot.is_open is False

    def test_calibrate_issues_one_decrement_per_tap_plus_margin(self):
        pot, trace, _ = build_pot()

        assert pot.calibrate() == 0

        assert falling_edges(trace) == TAP_COUNT
        assert ("UD", 0) in trace
        assert pot.position == 0

    def test_calibrate_never_raises_ud_high(self):
        pot, trace, _ = build_pot()

        pot.calibrate()

        # A single stray increment would leave the wiper off the RL end, which
        # is exactly the unknown state calibration exists to remove.
        assert [level for name, level in trace if name == "UD"] == [1, 0]

    def test_set_position_steps_up_by_the_delta(self):
        pot, trace, _ = build_pot()
        pot.calibrate()
        trace.clear()

        pot.set_position(30)

        assert falling_edges(trace) == 30
        assert ("UD", 1) in trace
        assert pot.position == 30

    def test_set_position_steps_down_by_the_delta(self):
        pot, trace, _ = build_pot()
        pot.calibrate()
        pot.set_position(30)
        trace.clear()

        pot.set_position(12)

        assert falling_edges(trace) == 18
        assert ("UD", 0) in trace

    def test_set_position_calibrates_first_when_wiper_is_unknown(self):
        pot, trace, _ = build_pot()

        pot.set_position(20)

        # 100 decrements to reach a known 0, then 20 increments back up.
        assert falling_edges(trace) == TAP_COUNT + 20
        assert pot.position == 20

    def test_repeating_a_position_touches_no_lines(self):
        pot, trace, _ = build_pot()
        pot.set_position(20)
        trace.clear()

        assert pot.set_position(20) == 20

        assert trace == []

    def test_deselect_without_store_leaves_inc_low_until_cs_is_high(self):
        pot, trace, _ = build_pot()
        pot.calibrate()
        trace.clear()

        pot.set_position(3)

        tail = trace[-3:]
        assert tail == [("INC", 0), ("CS", 1), ("INC", 1)]

    def test_store_deselects_with_inc_high(self):
        pot, trace, _ = build_pot()
        pot.calibrate()
        trace.clear()

        pot.set_position(3, store=True)

        assert trace[-2:] == [("INC", 1), ("CS", 1)]

    def test_store_waits_the_nvm_cycle(self):
        slept = []
        trace = []

        def factory(pin):
            return RecordingLine(str(pin), trace)

        pot = X9C104(
            cs_pin=7,
            inc_pin=8,
            ud_pin=9,
            output_factory=factory,
            sleep=slept.append,
            step_delay_s=0.001,
            store_delay_s=0.05,
        )
        pot.calibrate()
        slept.clear()

        pot.set_position(1, store=True)

        assert slept[-1] == 0.05

    def test_store_with_no_movement_still_commits(self):
        pot, trace, _ = build_pot()
        pot.set_position(20)
        trace.clear()

        pot.set_position(20, store=True)

        assert falling_edges(trace) == 0
        assert trace[-2:] == [("INC", 1), ("CS", 1)]

    def test_close_releases_lines_and_forgets_the_position(self):
        pot, _, lines = build_pot()
        pot.set_position(20)

        pot.close()

        assert all(line.closed for line in lines.values())
        assert pot.position is None
        assert pot.is_open is False

    def test_close_is_safe_before_open(self):
        pot, _, _ = build_pot()

        pot.close()

        assert pot.position is None

    def test_close_survives_a_line_that_raises(self):
        pot, _, lines = build_pot()
        pot.open()

        def boom():
            raise OSError("already released")

        lines["INC"].close = boom

        pot.close()

        assert lines["CS"].closed is True
        assert pot.is_open is False

    @pytest.mark.parametrize("bad", [-1, MAX_POSITION + 1, 1000])
    def test_out_of_range_positions_are_rejected(self, bad):
        pot, _, _ = build_pot()

        with pytest.raises(ValueError):
            pot.set_position(bad)

    @pytest.mark.parametrize("bad", [1.5, "40", None, True])
    def test_non_integer_positions_are_rejected(self, bad):
        pot, _, _ = build_pot()

        with pytest.raises(TypeError):
            pot.set_position(bad)

    def test_a_rejected_position_leaves_the_wiper_untouched(self):
        pot, trace, _ = build_pot()
        pot.set_position(20)
        trace.clear()

        with pytest.raises(ValueError):
            pot.set_position(500)

        assert trace == []
        assert pot.position == 20


class TestResistanceMaths:
    def test_position_zero_is_the_wiper_resistance_alone(self):
        assert resistance_ohms(0) == pytest.approx(x9c104.WIPER_OHMS)

    def test_top_position_is_the_full_element(self):
        assert resistance_ohms(MAX_POSITION) == pytest.approx(
            x9c104.WIPER_OHMS + x9c104.END_TO_END_OHMS
        )

    def test_resistance_rises_monotonically_with_position(self):
        values = [resistance_ohms(position) for position in range(TAP_COUNT)]

        assert values == sorted(values)

    def test_preamp_resistance_is_the_parallel_combination(self):
        # 100k pot in parallel with the board's 100k R3.
        assert preamp_feedback_ohms(MAX_POSITION) == pytest.approx(50_010, rel=1e-3)

    def test_lower_position_means_lower_gain(self):
        assert preamp_feedback_ohms(10) < preamp_feedback_ohms(80)

    def test_sensitivity_percent_spans_the_full_travel(self):
        assert sensitivity_percent(0) == 0.0
        assert sensitivity_percent(MAX_POSITION) == 100.0

    @pytest.mark.parametrize(
        "ohms,expected",
        [(0, 0), (33_000, 33), (47_000, 46), (100_000, 99), (1_000_000, MAX_POSITION), (-5, 0)],
    )
    def test_resistance_maps_back_to_the_nearest_tap(self, ohms, expected):
        assert position_for_resistance(ohms) == expected

    def test_the_documented_r17_values_round_trip(self):
        # The wiring guide tells builders to fit 47k or 33k; the tap the UI
        # lands on has to match that advice within one step (~1k).
        for ohms in (47_000, 33_000):
            assert resistance_ohms(position_for_resistance(ohms)) == pytest.approx(ohms, abs=1_100)


class TestMockParity:
    def test_mock_tracks_positions_like_the_driver(self):
        mock = MockX9C104()

        assert mock.position is None
        assert mock.calibrate() == 0
        assert mock.set_position(42) == 42
        assert mock.position == 42

    def test_mock_enforces_the_same_range(self):
        mock = MockX9C104()

        with pytest.raises(ValueError):
            mock.set_position(MAX_POSITION + 1)

    def test_mock_close_forgets_the_position(self):
        mock = MockX9C104()
        mock.set_position(10)

        mock.close()

        assert mock.position is None
        assert mock.is_open is False
