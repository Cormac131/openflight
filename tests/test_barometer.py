"""BMP580 transport, decoding, filtering, and reading-selection tests."""

import time

import pytest

from openflight.air_density import AirConditions
from openflight.barometer import (
    BMP580,
    AirSnapshot,
    BarometerService,
    BMP580IdentityError,
    BMP580NotReadyError,
    PressureSample,
)


def _pressure_bytes(pressure_pa: float) -> list[int]:
    """Encode Pa into the sensor's little-endian 24-bit, 2^-6 Pa format."""
    raw = round(pressure_pa * 64)
    return [raw & 0xFF, (raw >> 8) & 0xFF, (raw >> 16) & 0xFF]


def _temperature_bytes(temperature_c: float) -> list[int]:
    """Encode °C into the sensor's little-endian signed 24-bit, 2^-16 °C format."""
    raw = round(temperature_c * 65536) & 0xFFFFFF
    return [raw & 0xFF, (raw >> 8) & 0xFF, (raw >> 16) & 0xFF]


class FakeBus:
    """Minimal BMP580 stand-in driven by the datasheet register map.

    INT_STATUS.drdy_data_reg is only visible after INT_SOURCE.drdy_data_reg_en
    is written (BST-BMP581-DS004 4.7.1). That is the hardware timeout: the
    chip converts, but the status bit never appears if the source is left off.
    """

    def __init__(
        self,
        *,
        chip_id=0x50,
        status=BMP580.NVM_READY,
        pressure_pa=101325.0,
        temperature_c=20.0,
        drdy_after_polls=0,
        block_length=6,
    ):
        self.chip_id = chip_id
        self.status = status
        self.pressure_pa = pressure_pa
        self.temperature_c = temperature_c
        self.drdy_after_polls = drdy_after_polls
        self.block_length = block_length
        self.writes = []
        self.polls = 0
        self.closed = False
        self.drdy_enabled = False

    def read_byte_data(self, address, register):
        assert address in (BMP580.DEFAULT_ADDRESS, BMP580.ALTERNATE_ADDRESS)
        if register == BMP580.CHIP_ID:
            return self.chip_id
        if register == BMP580.STATUS:
            return self.status
        if register == BMP580.INT_STATUS:
            self.polls += 1
            if self.drdy_enabled and self.polls > self.drdy_after_polls:
                return BMP580.DRDY_DATA_REG
            return 0x00
        raise AssertionError(f"unexpected register read 0x{register:02x}")

    def write_byte_data(self, address, register, value):
        self.writes.append((address, register, value))
        if register == BMP580.INT_SOURCE:
            self.drdy_enabled = bool(value & BMP580.DRDY_DATA_REG_EN)

    def read_i2c_block_data(self, address, register, length):
        assert (address, register, length) == (
            BMP580.DEFAULT_ADDRESS,
            BMP580.TEMP_DATA_XLSB,
            6,
        )
        data = _temperature_bytes(self.temperature_c) + _pressure_bytes(self.pressure_pa)
        return data[: self.block_length]

    def close(self):
        self.closed = True


class TestBMP580Driver:
    def test_initializes_and_reads_pressure_and_temperature(self):
        bus = FakeBus(pressure_pa=98000.0, temperature_c=21.5)
        sensor = BMP580(bus=bus)

        sensor.initialize()
        sample = sensor.read(timestamp=123.0)
        sensor.close()

        assert sample.timestamp == 123.0
        assert sample.pressure_pa == pytest.approx(98000.0, abs=0.02)
        assert sample.temperature_c == pytest.approx(21.5, abs=0.001)
        assert bus.closed

    def test_initialize_soft_resets_then_configures_oversampling_and_standby(self):
        bus = FakeBus()
        BMP580(bus=bus).initialize()

        address = BMP580.DEFAULT_ADDRESS
        assert bus.writes == [
            (address, BMP580.CMD, BMP580.SOFT_RESET),
            (address, BMP580.INT_SOURCE, BMP580.DRDY_DATA_REG_EN),
            (address, BMP580.OSR_CONFIG, BMP580.OSR_CONFIG_VALUE),
            (address, BMP580.ODR_CONFIG, BMP580.DEEP_DIS | BMP580.MODE_STANDBY),
        ]

    def test_osr_config_enables_pressure_at_the_datasheet_bit_positions(self):
        # press_en is bit 6, osr_p bits 5:3, osr_t bits 2:0 (BST-BMP581-DS004).
        value = BMP580.OSR_CONFIG_VALUE
        assert value & (1 << 6)
        assert (value >> 3) & 0b111 == 0b100  # x16 pressure
        assert value & 0b111 == 0b001  # x2 temperature

    def test_read_triggers_a_forced_conversion(self):
        bus = FakeBus()
        sensor = BMP580(bus=bus)
        sensor.initialize()
        bus.writes.clear()

        sensor.read(timestamp=1.0)

        assert bus.writes == [
            (
                BMP580.DEFAULT_ADDRESS,
                BMP580.ODR_CONFIG,
                BMP580.DEEP_DIS | BMP580.MODE_FORCED,
            )
        ]

    def test_read_waits_for_data_ready(self):
        bus = FakeBus(drdy_after_polls=3)
        sensor = BMP580(bus=bus)
        sensor.initialize()

        sample = sensor.read(timestamp=1.0)

        assert bus.polls == 4
        assert sample.pressure_pa == pytest.approx(101325.0, abs=0.02)

    def test_read_times_out_when_conversion_never_completes(self, monkeypatch):
        bus = FakeBus(drdy_after_polls=10**9)
        sensor = BMP580(bus=bus)
        sensor.initialize()
        monkeypatch.setattr(BMP580, "CONVERSION_TIMEOUT_S", 0.02)

        with pytest.raises(BMP580NotReadyError):
            sensor.read(timestamp=1.0)

    @pytest.mark.parametrize("chip_id, name", [(0x50, "BMP580/BMP581"), (0x51, "BMP585")])
    def test_accepts_every_chip_in_the_family(self, chip_id, name):
        # The three parts share one register map and differ only in chip ID.
        sensor = BMP580(bus=FakeBus(chip_id=chip_id))
        sensor.initialize()
        assert sensor.chip_name == name

    def test_rejects_wrong_chip_id(self):
        sensor = BMP580(bus=FakeBus(chip_id=0x58))
        with pytest.raises(BMP580IdentityError, match="CHIP_ID"):
            sensor.initialize()

    def test_rejects_sensor_whose_nvm_is_not_ready(self):
        sensor = BMP580(bus=FakeBus(status=0x00))
        with pytest.raises(BMP580IdentityError, match="NVM"):
            sensor.initialize()

    def test_rejects_sensor_reporting_an_nvm_error(self):
        sensor = BMP580(bus=FakeBus(status=BMP580.NVM_READY | BMP580.NVM_ERROR))
        with pytest.raises(BMP580IdentityError, match="NVM"):
            sensor.initialize()

    def test_rejects_short_data_block(self):
        sensor = BMP580(bus=FakeBus(block_length=4))
        sensor.initialize()
        with pytest.raises(OSError, match="expected 6"):
            sensor.read(timestamp=1.0)

    def test_decodes_negative_temperature(self):
        bus = FakeBus(temperature_c=-12.25)
        sensor = BMP580(bus=bus)
        sensor.initialize()
        assert sensor.read(timestamp=1.0).temperature_c == pytest.approx(-12.25, abs=0.001)

    def test_close_returns_the_sensor_to_standby(self):
        bus = FakeBus()
        sensor = BMP580(bus=bus)
        sensor.initialize()
        bus.writes.clear()
        sensor.close()
        assert bus.writes == [
            (
                BMP580.DEFAULT_ADDRESS,
                BMP580.ODR_CONFIG,
                BMP580.DEEP_DIS | BMP580.MODE_STANDBY,
            )
        ]

    def test_close_is_idempotent(self):
        bus = FakeBus()
        sensor = BMP580(bus=bus)
        sensor.initialize()
        sensor.close()
        sensor.close()
        assert bus.closed

    def test_close_survives_a_sensor_that_has_gone_away(self):
        class DeadBus(FakeBus):
            def write_byte_data(self, address, register, value):
                raise OSError("device disappeared")

        bus = DeadBus()
        sensor = BMP580(bus=bus)
        sensor.close()
        assert bus.closed

    def test_alternate_address_is_supported(self):
        sensor = BMP580(bus=FakeBus(), address=BMP580.ALTERNATE_ADDRESS)
        sensor.initialize()
        assert sensor.address == 0x46


class StubSensor:
    """
    Sensor stub for the service tests.

    With no `samples`, it behaves like the real driver and stamps each reading
    with the current time — which is what the threaded tests need, since the
    service ages readings against the wall clock.
    """

    def __init__(self, samples=None, error=None, pressure_pa=101325.0, temperature_c=20.0):
        self.samples = list(samples or [])
        self.error = error
        self.pressure_pa = pressure_pa
        self.temperature_c = temperature_c
        self.initialized = False
        self.closed = False
        self.index = 0

    def initialize(self):
        self.initialized = True

    def read(self, *, timestamp=None):
        if self.error is not None:
            raise self.error
        if self.samples:
            sample = self.samples[min(self.index, len(self.samples) - 1)]
            self.index += 1
            return sample
        self.index += 1
        return PressureSample(
            timestamp=time.time() if timestamp is None else timestamp,
            pressure_pa=self.pressure_pa,
            temperature_c=self.temperature_c,
        )

    def close(self):
        self.closed = True


def _samples(count, *, pressure_pa=101325.0, temperature_c=20.0, start=1000.0):
    return [
        PressureSample(
            timestamp=start + index, pressure_pa=pressure_pa, temperature_c=temperature_c
        )
        for index in range(count)
    ]


class TestBarometerService:
    def test_no_snapshot_until_the_window_fills(self):
        service = BarometerService(StubSensor(), window_samples=3)
        assert service.add_sample(_samples(1)[0]) is None
        assert service.add_sample(_samples(1, start=1001.0)[0]) is None
        assert service.add_sample(_samples(1, start=1002.0)[0]) is not None

    def test_snapshot_carries_density_and_corrected_temperature(self):
        service = BarometerService(
            StubSensor(), window_samples=2, temperature_offset_c=-4.0
        )
        for sample in _samples(2, pressure_pa=98000.0, temperature_c=24.0):
            snapshot = service.add_sample(sample)

        assert isinstance(snapshot, AirSnapshot)
        assert snapshot.raw_temperature_c == pytest.approx(24.0)
        assert snapshot.temperature_c == pytest.approx(20.0)
        # Density must use the corrected temperature, not the die reading.
        assert snapshot.density_kg_m3 == pytest.approx(98000.0 / (287.0528 * 293.15), rel=1e-6)

    def test_median_rejects_a_single_glitched_sample(self):
        service = BarometerService(StubSensor(), window_samples=3)
        readings = [101325.0, 40000000.0, 101325.0]
        for index, pressure in enumerate(readings):
            snapshot = service.add_sample(
                PressureSample(timestamp=1000.0 + index, pressure_pa=pressure, temperature_c=20.0)
            )
        assert snapshot.pressure_pa == pytest.approx(101325.0)

    def test_implausible_window_is_rejected_rather_than_turned_into_a_density(self):
        # A disconnected sensor reading zeros must not become "very thin air".
        service = BarometerService(StubSensor(), window_samples=2)
        for sample in _samples(2, pressure_pa=0.0):
            snapshot = service.add_sample(sample)
        assert snapshot is None
        assert service.reading_for_shot(now=1002.0).status == "sensor_error"

    def test_reading_for_shot_reports_no_reading_before_any_sample(self):
        service = BarometerService(StubSensor())
        selection = service.reading_for_shot(now=1000.0)
        assert selection.snapshot is None
        assert selection.status == "no_reading"

    def test_fresh_reading_is_ok_with_its_age(self):
        service = BarometerService(StubSensor(), window_samples=2)
        for sample in _samples(2):
            service.add_sample(sample)
        selection = service.reading_for_shot(now=1030.0)
        assert selection.status == "ok"
        assert selection.age_s == pytest.approx(29.0)

    def test_reading_older_than_the_limit_is_stale(self):
        service = BarometerService(StubSensor(), window_samples=2, max_reading_age_s=60.0)
        for sample in _samples(2):
            service.add_sample(sample)
        selection = service.reading_for_shot(now=1200.0)
        assert selection.snapshot is None
        assert selection.status == "stale"

    def test_current_conditions_uses_measured_pressure_and_is_labelled_sensor(self):
        service = BarometerService(StubSensor(), window_samples=2)
        for sample in _samples(2, pressure_pa=98000.0, temperature_c=15.0):
            service.add_sample(sample)

        conditions = service.current_conditions(now=1002.0)
        assert isinstance(conditions, AirConditions)
        assert conditions.pressure_pa == pytest.approx(98000.0)
        assert conditions.source == "sensor"

    def test_configured_elevation_is_metadata_and_never_changes_density(self):
        """
        A barometer's elevation is an assumption, its pressure is a measurement.
        Two services at wildly different configured elevations must agree on
        density when they read the same pressure.
        """
        conditions = []
        for elevation_m in (0.0, 1609.0):
            service = BarometerService(
                StubSensor(), window_samples=2, elevation_m=elevation_m
            )
            for sample in _samples(2, pressure_pa=98000.0, temperature_c=15.0):
                service.add_sample(sample)
            conditions.append(service.current_conditions(now=1002.0))

        assert conditions[0].density_kg_m3 == pytest.approx(conditions[1].density_kg_m3)
        assert conditions[1].elevation_m == pytest.approx(1609.0)

    def test_current_conditions_is_none_when_the_reading_is_stale(self):
        service = BarometerService(StubSensor(), window_samples=2, max_reading_age_s=10.0)
        for sample in _samples(2):
            service.add_sample(sample)
        assert service.current_conditions(now=5000.0) is None

    def test_start_initializes_and_stop_closes_the_sensor(self):
        sensor = StubSensor(pressure_pa=98000.0)
        service = BarometerService(sensor, sample_hz=50.0, window_samples=1)
        service.start()
        try:
            selection = service.wait_for_reading(timeout_s=2.0)
        finally:
            service.stop()

        assert sensor.initialized
        assert sensor.closed
        assert selection.snapshot is not None

    def test_sample_errors_are_recorded_without_stopping_the_loop(self):
        sensor = StubSensor(error=OSError("i2c read failed"))
        service = BarometerService(sensor, sample_hz=50.0, window_samples=1)
        service.start()
        try:
            service.wait_for_reading(timeout_s=0.5)
        finally:
            service.stop()

        assert service.last_error is not None
        assert "i2c read failed" in service.last_error

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"sample_hz": 0.0},
            {"sample_hz": -1.0},
            {"window_samples": 0},
            {"max_reading_age_s": 0.0},
        ],
    )
    def test_rejects_invalid_configuration(self, kwargs):
        with pytest.raises(ValueError):
            BarometerService(StubSensor(), **kwargs)


class TestSnapshotSerialisation:
    def test_to_dict_is_rounded_and_includes_hpa(self):
        service = BarometerService(StubSensor(), window_samples=1)
        snapshot = service.add_sample(
            PressureSample(timestamp=1000.0, pressure_pa=98123.456, temperature_c=20.0)
        )
        data = snapshot.to_dict()
        assert data["pressure_hpa"] == pytest.approx(981.23, abs=0.01)
        assert data["density_kg_m3"] == pytest.approx(snapshot.density_kg_m3, abs=0.0001)

    def test_selection_to_dict_marks_application_and_status(self):
        service = BarometerService(StubSensor(), window_samples=1)
        service.add_sample(_samples(1)[0])
        data = service.reading_for_shot(now=1000.0).to_dict()
        assert data["applied"] is True
        assert data["status"] == "ok"

    def test_selection_to_dict_when_nothing_applied(self):
        data = BarometerService(StubSensor()).reading_for_shot(now=1.0).to_dict()
        assert data["applied"] is False
        assert data["age_s"] is None


class TestLastKnownConditions:
    def test_none_before_any_reading(self):
        assert BarometerService(StubSensor()).last_known_conditions() is None

    def test_returns_a_reading_that_is_far_too_old_to_be_fresh(self):
        service = BarometerService(StubSensor(), window_samples=2, max_reading_age_s=1.0)
        for sample in _samples(2, pressure_pa=83400.0, temperature_c=20.0):
            service.add_sample(sample)

        # Fresh selection refuses it; last-known still hands it over.
        assert service.current_conditions(now=1e9) is None
        remembered = service.last_known_conditions()
        assert remembered is not None
        assert remembered.pressure_pa == pytest.approx(83400.0)

    def test_is_labelled_sensor_stale_not_sensor(self):
        service = BarometerService(StubSensor(), window_samples=1)
        service.add_sample(_samples(1)[0])
        assert service.last_known_conditions().source == "sensor_stale"

    def test_matches_the_fresh_reading_apart_from_provenance(self):
        service = BarometerService(StubSensor(), window_samples=1)
        service.add_sample(_samples(1, pressure_pa=83400.0)[0])

        fresh = service.current_conditions(now=1000.0)
        remembered = service.last_known_conditions()
        assert remembered.density_kg_m3 == pytest.approx(fresh.density_kg_m3)
        assert fresh.source == "sensor"
        assert remembered.source == "sensor_stale"
