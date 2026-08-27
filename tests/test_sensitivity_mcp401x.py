"""MCP4017/18/19 driver: single-byte protocol, volatile wiper, resistance maths."""

import pytest

from openflight.sensitivity.mcp401x import (
    DEFAULT_ADDRESS,
    DEFAULT_END_TO_END_OHMS,
    DEFAULT_SERIES_OHMS,
    DEFAULT_WIPER_OHMS,
    MAX_POSITION,
    MCP401X,
    POSITION_COUNT,
    POWER_ON_POSITION,
    MockMCP401X,
    position_for_resistance,
    preamp_feedback_ohms,
    resistance_ohms,
    sensitivity_percent,
    validate_address,
)


class FakeBus:
    """An I2C bus that records register-less byte traffic."""

    def __init__(self, *, value=POWER_ON_POSITION, fail=False):
        self.value = value
        self.writes = []
        self.reads = 0
        self.closed = False
        self.fail = fail

    def read_byte(self, address):
        del address
        if self.fail:
            raise OSError("no device at address")
        self.reads += 1
        return self.value

    def write_byte(self, address, value):
        del address
        if self.fail:
            raise OSError("no device at address")
        self.writes.append(value)
        self.value = value

    def close(self):
        self.closed = True


def build(**kwargs):
    bus = FakeBus()
    return MCP401X(bus=bus, **kwargs), bus


class TestProtocol:
    def test_a_write_is_one_byte_with_no_register_address(self):
        # The datasheet's write is the slave address then a single data byte.
        pot, bus = build()
        pot.open()

        pot.set_position(96)

        assert bus.writes == [96]

    def test_a_read_returns_the_wiper_directly(self):
        pot, bus = build()
        pot.open()
        bus.value = 33

        assert pot.position == 33

    def test_the_msb_of_a_read_is_ignored(self):
        # "the MSb of the Data Byte is a don't care since the wiper register is
        # only 7-bits wide"
        pot, bus = build()
        pot.open()
        bus.value = 0x80 | 40

        assert pot.position == 40

    def test_open_probes_by_reading(self):
        # There is no config register to write, so the read is the presence check.
        pot, bus = build()

        pot.open()

        assert bus.reads == 1
        assert bus.writes == []

    def test_a_missing_device_says_the_address_is_fixed(self):
        pot = MCP401X(bus=FakeBus(fail=True))

        with pytest.raises(RuntimeError, match="no address pins"):
            pot.open()

    def test_position_is_none_before_open(self):
        pot, _ = build()

        assert pot.position is None

    def test_setting_a_position_before_open_is_refused(self):
        pot, _ = build()

        with pytest.raises(RuntimeError, match="not open"):
            pot.set_position(10)

    @pytest.mark.parametrize("bad", [-1, POSITION_COUNT, 1000])
    def test_out_of_range_positions_are_rejected(self, bad):
        pot, bus = build()
        pot.open()
        bus.writes.clear()

        with pytest.raises(ValueError):
            pot.set_position(bad)

        assert bus.writes == []

    @pytest.mark.parametrize("bad", [1.5, "40", None, True])
    def test_non_integer_positions_are_rejected(self, bad):
        pot, _ = build()
        pot.open()

        with pytest.raises(TypeError):
            pot.set_position(bad)

    def test_close_leaves_a_caller_supplied_bus_alone(self):
        pot, bus = build()
        pot.open()

        pot.close()

        assert bus.closed is False
        assert pot.is_open is False


class TestVolatility:
    def test_the_driver_declares_that_it_does_not_persist(self):
        # The service keeps a file for parts like this; getting the flag wrong
        # would silently lose the user's setting on every power cycle.
        assert MCP401X.persists_in_hardware is False
        assert MockMCP401X.persists_in_hardware is False

    def test_a_store_request_is_accepted_and_ignored(self):
        pot, bus = build()
        pot.open()

        pot.set_position(50, store=True)

        assert bus.writes == [50]

    def test_the_mock_comes_up_at_mid_scale_like_the_real_part(self):
        # "Power-on Default Wiper Setting (Mid-scale)"
        mock = MockMCP401X()
        mock.open()

        assert mock.position == POWER_ON_POSITION

    def test_the_mock_forgets_across_a_power_cycle(self):
        mock = MockMCP401X()
        mock.open()
        mock.set_position(99, store=True)

        mock.close()
        mock.open()

        assert mock.position == POWER_ON_POSITION


class TestAddress:
    def test_the_fixed_address_is_accepted(self):
        assert validate_address(DEFAULT_ADDRESS) == 0x2F

    @pytest.mark.parametrize("address", [0x28, 0x2E, 0x30, 0x48, 0x18])
    def test_any_other_address_is_refused(self, address):
        # No address pins, so an override could only reach another device --
        # 0x28 is the DS3502, 0x48 the ADC, 0x18 the inclinometer.
        with pytest.raises(ValueError, match="fixed"):
            validate_address(address)


class TestResistanceMaths:
    def test_step_zero_is_the_wiper_resistance(self):
        assert resistance_ohms(0) == pytest.approx(DEFAULT_WIPER_OHMS)

    def test_top_step_is_the_full_element(self):
        assert resistance_ohms(MAX_POSITION) == pytest.approx(
            DEFAULT_WIPER_OHMS + DEFAULT_END_TO_END_OHMS
        )

    def test_no_series_resistor_is_needed_by_default(self):
        # A 100k part reaches R17's range unaided; that is the whole point of it.
        assert DEFAULT_SERIES_OHMS == 0.0

    def test_resistance_rises_monotonically(self):
        values = [resistance_ohms(step) for step in range(POSITION_COUNT)]

        assert values == sorted(values)

    def test_the_documented_r17_values_are_reachable(self):
        # 33k and 47k both land inside the span, which the 10k DS3502 could not
        # manage without a series resistor.
        for ohms in (33_000, 47_000):
            step = position_for_resistance(ohms)
            assert 0 < step < MAX_POSITION
            assert resistance_ohms(step) == pytest.approx(ohms, abs=800)

    def test_preamp_resistance_is_the_parallel_combination(self):
        assert preamp_feedback_ohms(MAX_POSITION) == pytest.approx(50_025, rel=1e-3)

    def test_lower_step_means_lower_gain(self):
        assert preamp_feedback_ohms(10) < preamp_feedback_ohms(120)

    def test_sensitivity_percent_spans_the_full_travel(self):
        assert sensitivity_percent(0) == 0.0
        assert sensitivity_percent(MAX_POSITION) == 100.0

    def test_a_smaller_family_member_can_be_configured(self):
        # The family also ships 5k/10k/50k.
        assert resistance_ohms(MAX_POSITION, end_to_end_ohms=10_000.0) == pytest.approx(
            DEFAULT_WIPER_OHMS + 10_000.0
        )


class TestGainRange:
    def test_the_span_gives_a_closed_loop_real_authority(self):
        """The DS3502's 10k behind a 33k series resistor moves the preamp leg
        by only ~1.2x, which is narrower than a 60-80% target band. A 100k part
        with no series resistor is not remotely so limited."""
        pot = MCP401X(bus=FakeBus())

        assert pot.gain_range() > 10.0

    def test_the_model_agrees_with_the_module_functions(self):
        pot = MCP401X(bus=FakeBus())

        assert pot.resistance_at(64) == pytest.approx(resistance_ohms(64))
        assert pot.preamp_at(64) == pytest.approx(preamp_feedback_ohms(64))
        assert pot.step_for_resistance(47_000) == position_for_resistance(47_000)
