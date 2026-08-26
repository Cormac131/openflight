"""DS3502 I2C driver: register access, readback, EEPROM commits, resistance maths."""

import pytest

from openflight.sensitivity import ds3502
from openflight.sensitivity.ds3502 import (
    DEFAULT_ADDRESS,
    DEFAULT_SERIES_OHMS,
    DS3502,
    MAX_POSITION,
    POSITION_COUNT,
    MockDS3502,
    position_for_resistance,
    preamp_feedback_ohms,
    resistance_ohms,
    sensitivity_percent,
    validate_address,
)


class FakeBus:
    """An I2C bus that records traffic and serves register reads."""

    def __init__(self, *, fail_on_write=False):
        self.registers = {ds3502.REG_WIPER: 0, ds3502.REG_CONTROL: 0}
        self.writes = []
        self.closed = False
        self.fail_on_write = fail_on_write

    def read_byte_data(self, address, register):
        del address
        return self.registers[register]

    def write_byte_data(self, address, register, value):
        del address
        if self.fail_on_write:
            raise OSError("no device at address")
        self.writes.append((register, value))
        self.registers[register] = value

    def close(self):
        self.closed = True


def build(**kwargs):
    bus = FakeBus()
    pot = DS3502(bus=bus, sleep=lambda _s: None, **kwargs)
    return pot, bus


class TestOpen:
    def test_open_selects_volatile_write_mode(self):
        pot, bus = build()

        pot.open()

        # Volatile by default: EEPROM endurance is finite and a commit is opt-in.
        assert bus.writes == [(ds3502.REG_CONTROL, ds3502.CONTROL_VOLATILE_ONLY)]

    def test_open_reports_a_missing_device_with_the_address_probed(self):
        bus = FakeBus(fail_on_write=True)
        pot = DS3502(bus=bus, address=0x2A)

        with pytest.raises(RuntimeError, match="0x2a"):
            pot.open()

    def test_a_failed_open_leaves_the_driver_closed(self):
        pot = DS3502(bus=FakeBus(fail_on_write=True))

        with pytest.raises(RuntimeError):
            pot.open()

        assert pot.is_open is False

    def test_the_probe_error_names_i2cdetect(self):
        pot = DS3502(bus=FakeBus(fail_on_write=True), bus_number=4)

        with pytest.raises(RuntimeError, match="i2cdetect -y 4"):
            pot.open()


class TestPosition:
    def test_position_is_read_back_from_the_chip(self):
        pot, bus = build()
        pot.open()
        bus.registers[ds3502.REG_WIPER] = 77

        # Not a remembered value: the chip is asked every time.
        assert pot.position == 77

    def test_position_is_none_before_open(self):
        pot, _ = build()

        assert pot.position is None

    def test_set_position_writes_the_wiper_register(self):
        pot, bus = build()
        pot.open()
        bus.writes.clear()

        assert pot.set_position(64) == 64

        assert bus.writes == [(ds3502.REG_WIPER, 64)]
        assert pot.position == 64

    def test_set_position_without_store_never_touches_the_control_register(self):
        pot, bus = build()
        pot.open()
        bus.writes.clear()

        pot.set_position(10)

        assert all(register != ds3502.REG_CONTROL for register, _ in bus.writes)

    def test_storing_commits_then_restores_volatile_mode(self):
        pot, bus = build()
        pot.open()
        bus.writes.clear()

        pot.set_position(90, store=True)

        assert bus.writes == [
            (ds3502.REG_WIPER, 90),
            (ds3502.REG_CONTROL, 0x00),
            (ds3502.REG_WIPER, 90),
            (ds3502.REG_CONTROL, ds3502.CONTROL_VOLATILE_ONLY),
        ]

    def test_storing_waits_for_the_eeprom_cycle(self):
        slept = []
        pot = DS3502(bus=FakeBus(), sleep=slept.append)
        pot.open()

        pot.set_position(5, store=True)

        assert ds3502.EEPROM_WRITE_DELAY_S in slept

    def test_setting_a_position_before_open_is_refused(self):
        pot, _ = build()

        with pytest.raises(RuntimeError, match="not open"):
            pot.set_position(10)

    @pytest.mark.parametrize("bad", [-1, POSITION_COUNT, 1000])
    def test_out_of_range_positions_are_rejected(self, bad):
        pot, _ = build()
        pot.open()

        with pytest.raises(ValueError):
            pot.set_position(bad)

    @pytest.mark.parametrize("bad", [1.5, "40", None, True])
    def test_non_integer_positions_are_rejected(self, bad):
        pot, _ = build()
        pot.open()

        with pytest.raises(TypeError):
            pot.set_position(bad)

    def test_a_rejected_position_writes_nothing(self):
        pot, bus = build()
        pot.open()
        bus.writes.clear()

        with pytest.raises(ValueError):
            pot.set_position(500)

        assert bus.writes == []


class TestClose:
    def test_close_releases_a_bus_the_driver_opened(self):
        bus = FakeBus()
        pot = DS3502(bus_number=1)
        pot._bus = bus  # pylint: disable=protected-access

        pot.close()

        assert bus.closed is True

    def test_close_leaves_a_caller_supplied_bus_alone(self):
        # The bus may be shared with the inclinometer; closing it would take
        # that down too.
        pot, bus = build()
        pot.open()

        pot.close()

        assert bus.closed is False
        assert pot.is_open is False

    def test_close_is_idempotent(self):
        pot, _ = build()
        pot.open()

        pot.close()
        pot.close()

        assert pot.position is None


class TestAddresses:
    @pytest.mark.parametrize("address", [0x28, 0x29, 0x2A, 0x2B])
    def test_the_four_jumper_addresses_are_accepted(self, address):
        assert validate_address(address) == address

    @pytest.mark.parametrize("address", [0x27, 0x2C, 0x18, 0x36])
    def test_other_addresses_are_refused(self, address):
        # 0x18 and 0x36 are the inclinometer and UPS gauge; a typo landing
        # there would present as a bus error at an unrelated device.
        with pytest.raises(ValueError):
            validate_address(address)

    def test_the_default_address_is_valid(self):
        assert validate_address(DEFAULT_ADDRESS) == DEFAULT_ADDRESS


class TestResistanceMaths:
    def test_step_zero_is_the_series_resistor_alone(self):
        assert resistance_ohms(0) == pytest.approx(DEFAULT_SERIES_OHMS)

    def test_top_step_adds_the_full_element(self):
        assert resistance_ohms(MAX_POSITION) == pytest.approx(
            DEFAULT_SERIES_OHMS + ds3502.END_TO_END_OHMS
        )

    def test_resistance_rises_monotonically(self):
        values = [resistance_ohms(step) for step in range(POSITION_COUNT)]

        assert values == sorted(values)

    def test_the_series_resistor_shifts_the_whole_span(self):
        assert resistance_ohms(0, 39_000.0) == pytest.approx(39_000.0)
        assert resistance_ohms(MAX_POSITION, 39_000.0) == pytest.approx(49_000.0)

    def test_preamp_resistance_is_the_parallel_combination(self):
        # 43k R17 (33k series + full 10k wiper) against the board's 100k R3.
        assert preamp_feedback_ohms(MAX_POSITION) == pytest.approx(30_070, rel=1e-3)

    def test_lower_step_means_lower_gain(self):
        assert preamp_feedback_ohms(10) < preamp_feedback_ohms(120)

    def test_the_span_reaches_the_documented_noisy_room_value(self):
        # The guide's aggressive R17 is 33k; the default series resistor is
        # chosen so step 0 lands exactly there.
        assert resistance_ohms(0) == pytest.approx(33_000, abs=100)

    def test_sensitivity_percent_spans_the_full_travel(self):
        assert sensitivity_percent(0) == 0.0
        assert sensitivity_percent(MAX_POSITION) == 100.0

    def test_resolution_is_finer_than_a_hundred_ohms(self):
        step = resistance_ohms(1) - resistance_ohms(0)

        assert step < 100

    @pytest.mark.parametrize("ohms,expected", [(33_000, 0), (38_000, 64), (43_000, MAX_POSITION)])
    def test_resistance_maps_back_to_the_nearest_step(self, ohms, expected):
        assert position_for_resistance(ohms) == expected

    @pytest.mark.parametrize("ohms", [0, 1_000_000, -5])
    def test_targets_outside_the_span_clamp_to_an_end(self, ohms):
        assert 0 <= position_for_resistance(ohms) <= MAX_POSITION


class TestMockParity:
    def test_mock_comes_up_at_mid_scale(self):
        mock = MockDS3502()
        mock.open()

        assert mock.position == MAX_POSITION // 2

    def test_mock_enforces_the_same_range(self):
        mock = MockDS3502()
        mock.open()

        with pytest.raises(ValueError):
            mock.set_position(POSITION_COUNT)

    def test_mock_remembers_a_stored_position_across_a_close(self):
        mock = MockDS3502()
        mock.open()
        mock.set_position(99, store=True)
        mock.close()

        mock.open()

        assert mock.position == 99

    def test_mock_forgets_an_unstored_position(self):
        mock = MockDS3502()
        mock.open()
        mock.set_position(99)
        mock.close()

        mock.open()

        assert mock.position == MAX_POSITION // 2
