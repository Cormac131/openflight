"""Tests for X1202/X1206 battery and adapter telemetry."""

import sys
from types import ModuleType

from openflight import gpio_factory
from openflight.power import GeekwormPowerReader, PowerMonitor, PowerSample, PowerState


class FakeBus:
    def __init__(self, registers):
        self.registers = registers
        self.closed = False

    def read_i2c_block_data(self, address, register, length):
        assert address == 0x36
        assert length == 2
        return self.registers[register]

    def close(self):
        self.closed = True


class FakeInput:
    def __init__(self, value):
        self.value = value
        self.closed = False

    def close(self):
        self.closed = True


class SequenceReader:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.closed = False

    def read(self):
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


def test_reader_decodes_max17043_voltage_soc_and_adapter_state():
    bus = FakeBus({0x02: [0xC3, 0x00], 0x04: [65, 128]})
    ac_input = FakeInput(1)
    reader = GeekwormPowerReader(bus=bus, ac_input=ac_input)

    sample = reader.read()

    assert sample.battery_voltage_v == 3.9
    assert sample.battery_percent == 65.5
    assert sample.external_power is True


def test_reader_releases_i2c_and_gpio_resources_once():
    bus = FakeBus({0x02: [0, 0], 0x04: [0, 0]})
    ac_input = FakeInput(0)
    reader = GeekwormPowerReader(bus=bus, ac_input=ac_input)

    reader.close()
    reader.close()

    assert bus.closed is True
    assert ac_input.closed is True


def test_reader_treats_gpio6_as_active_high_with_a_pull_up(monkeypatch):
    created = {}

    class SpyInput(FakeInput):
        def __init__(self, pin, **kwargs):
            super().__init__(value=1)
            created.update(pin=pin, **kwargs)

    fake_gpiozero = ModuleType("gpiozero")
    fake_gpiozero.DigitalInputDevice = SpyInput
    monkeypatch.setitem(sys.modules, "gpiozero", fake_gpiozero)
    monkeypatch.setattr(gpio_factory, "ensure_lgpio_pin_factory", lambda: None)

    reader = GeekwormPowerReader(
        bus=FakeBus({0x02: [0xC3, 0x00], 0x04: [50, 0]}),
    )

    assert created == {"pin": 6, "pull_up": True, "active_state": True}
    assert reader.read().external_power is True


def test_monitor_classifies_plugged_low_and_critical_states():
    samples = [
        PowerSample(50.0, 3.8, True),
        PowerSample(20.0, 3.5, False),
        PowerSample(10.0, 3.3, False),
    ]

    assert [PowerMonitor._state_for(sample) for sample in samples] == [
        PowerState.PLUGGED_IN,
        PowerState.LOW,
        PowerState.CRITICAL,
    ]


def test_monitor_recovers_after_a_read_failure_and_logs_transitions():
    readers = [
        SequenceReader([OSError("I2C device 0x36 missing")]),
        SequenceReader([PowerSample(42.25, 3.72, False)]),
    ]
    emitted = []
    logged = []
    now = iter([0.0, 1.0])
    monitor = PowerMonitor(
        on_status=emitted.append,
        on_log=logged.append,
        reader_factory=lambda: readers.pop(0),
        clock=lambda: next(now),
    )

    unavailable = monitor.poll_once()
    recovered = monitor.poll_once()

    assert unavailable.state is PowerState.UNAVAILABLE
    assert "0x36 missing" in unavailable.error
    assert recovered.state is PowerState.ON_BATTERY
    assert recovered.battery_percent == 42.2
    assert emitted == [unavailable, recovered]
    assert logged == [unavailable, recovered]


def test_monitor_throttles_unchanged_session_log_samples():
    reader = SequenceReader(
        [
            PowerSample(80.0, 4.0, False),
            PowerSample(79.0, 3.99, False),
            PowerSample(78.0, 3.98, False),
        ]
    )
    logged = []
    now = iter([0.0, 5.0, 65.0])
    monitor = PowerMonitor(
        on_status=lambda _status: None,
        on_log=logged.append,
        reader_factory=lambda: reader,
        log_interval_s=60.0,
        clock=lambda: next(now),
    )

    monitor.poll_once()
    monitor.poll_once()
    monitor.poll_once()

    assert [status.battery_percent for status in logged] == [80.0, 78.0]
