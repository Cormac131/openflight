"""Envelope capture: ADS1115 register access and per-impact peak selection."""

import pytest

from openflight.sensitivity import ads1115
from openflight.sensitivity.ads1115 import (
    ADS1115,
    DEFAULT_ADDRESS,
    MockADS1115,
    build_config,
    counts_to_volts,
    validate_address,
)
from openflight.sensitivity.envelope import EnvelopeMonitor


class FakeBus:
    """An I2C bus that records writes and serves a scripted conversion."""

    def __init__(self, *, counts=0, fail_on_write=False):
        self.writes = []
        self.counts = counts
        self.closed = False
        self.fail_on_write = fail_on_write

    def write_i2c_block_data(self, address, register, data):
        del address
        if self.fail_on_write:
            raise OSError("no device at address")
        self.writes.append((register, list(data)))

    def read_i2c_block_data(self, address, register, length):
        del address, register, length
        raw = self.counts & 0xFFFF
        return [(raw >> 8) & 0xFF, raw & 0xFF]

    def close(self):
        self.closed = True


class TestConfig:
    def test_the_config_selects_continuous_single_ended_reads(self):
        config = build_config(channel=0, full_scale_volts=4.096, data_rate=860)

        assert config & 0x7000 == 0x4000  # MUX = AIN0 vs GND
        assert config & 0x0E00 == 0x0200  # PGA = +/-4.096V
        assert config & 0x0100 == 0x0000  # MODE = continuous
        assert config & 0x00E0 == 0x00E0  # DR = 860 SPS
        assert config & 0x0003 == 0x0003  # comparator disabled

    @pytest.mark.parametrize("channel", [0, 1, 2, 3])
    def test_every_single_ended_channel_is_selectable(self, channel):
        config = build_config(channel=channel, full_scale_volts=4.096, data_rate=860)

        assert (config >> 12) & 0x07 == 0x04 + channel

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"channel": 4},
            {"channel": -1},
            {"full_scale_volts": 3.3},
            {"data_rate": 1000},
        ],
    )
    def test_settings_the_part_does_not_offer_are_refused(self, kwargs):
        base = {"channel": 0, "full_scale_volts": 4.096, "data_rate": 860}
        with pytest.raises(ValueError):
            build_config(**{**base, **kwargs})

    def test_a_bad_setting_is_caught_at_construction(self):
        # Not on the first sample, which happens inside a background thread.
        with pytest.raises(ValueError):
            ADS1115(bus=FakeBus(), data_rate=999)


class TestConversion:
    @pytest.mark.parametrize(
        "counts,expected",
        [(0, 0.0), (32767, 4.096), (16384, 2.048), (-32768, -4.096)],
    )
    def test_counts_convert_to_volts(self, counts, expected):
        assert counts_to_volts(counts, 4.096) == pytest.approx(expected, rel=1e-3)

    def test_a_reading_is_decoded_big_endian(self):
        bus = FakeBus(counts=16384)
        adc = ADS1115(bus=bus)
        adc.open()

        assert adc.read_volts() == pytest.approx(2.048, rel=1e-3)

    def test_a_negative_reading_is_sign_extended(self):
        # The conversion register is signed; an unsigned read would turn a
        # small negative excursion into a full-scale peak.
        bus = FakeBus(counts=-8192 & 0xFFFF)
        adc = ADS1115(bus=bus)
        adc.open()

        assert adc.read_volts() == pytest.approx(-1.024, rel=1e-3)

    def test_reading_before_open_is_refused(self):
        with pytest.raises(RuntimeError, match="not open"):
            ADS1115(bus=FakeBus()).read_volts()


class TestOpen:
    def test_open_writes_the_config_register(self):
        bus = FakeBus()
        adc = ADS1115(bus=bus, channel=0, full_scale_volts=4.096, data_rate=860)

        adc.open()

        assert bus.writes[0][0] == ads1115.REG_CONFIG

    def test_a_missing_device_names_the_address_probed(self):
        adc = ADS1115(bus=FakeBus(fail_on_write=True), address=0x4A)

        with pytest.raises(RuntimeError, match="0x4a"):
            adc.open()

    def test_close_leaves_a_caller_supplied_bus_alone(self):
        bus = FakeBus()
        adc = ADS1115(bus=bus)
        adc.open()

        adc.close()

        assert bus.closed is False
        assert adc.is_open is False


class TestAddresses:
    @pytest.mark.parametrize("address", [0x48, 0x49, 0x4A, 0x4B])
    def test_the_four_addr_options_are_accepted(self, address):
        assert validate_address(address) == address

    @pytest.mark.parametrize("address", [0x47, 0x4C, 0x28, 0x18])
    def test_other_addresses_are_refused(self, address):
        # 0x28 and 0x18 are the digipot and the inclinometer.
        with pytest.raises(ValueError):
            validate_address(address)

    def test_the_default_address_is_valid(self):
        assert validate_address(DEFAULT_ADDRESS) == DEFAULT_ADDRESS


class TestPeakSelection:
    def build(self, **kwargs):
        return EnvelopeMonitor(MockADS1115(), full_scale_volts=3.3, **kwargs)

    def test_it_finds_the_largest_sample_in_the_window(self):
        monitor = self.build()
        for offset, volts in ((-0.05, 0.5), (0.0, 2.4), (0.05, 1.1)):
            monitor.add_sample(volts, timestamp=100.0 + offset)

        peak = monitor.peak_for_impact(100.0)

        assert peak.volts == pytest.approx(2.4)
        assert peak.sample_count == 3

    def test_samples_outside_the_window_are_ignored(self):
        # A neighbouring strike must not be mistaken for this one's peak.
        monitor = self.build(lookback_s=0.1, lookahead_s=0.1)
        monitor.add_sample(0.4, timestamp=100.0)
        monitor.add_sample(3.2, timestamp=100.5)

        assert monitor.peak_for_impact(100.0).volts == pytest.approx(0.4)

    def test_it_looks_backwards_as_well_as_forwards(self):
        # The peak usually precedes the timestamp the pipeline reports.
        monitor = self.build(lookback_s=0.15)
        monitor.add_sample(2.0, timestamp=99.9)

        assert monitor.peak_for_impact(100.0).volts == pytest.approx(2.0)

    def test_an_empty_window_reports_nothing_rather_than_guessing(self):
        # A fabricated peak would steer the gain controller.
        assert self.build().peak_for_impact(100.0) is None

    def test_the_fraction_is_of_the_detector_supply_not_the_adc_range(self):
        # The detector clips at its own rail; the ADC's range is wider, and
        # scaling to it would understate how close to clipping a peak is.
        monitor = self.build()
        monitor.add_sample(3.3, timestamp=100.0)

        assert monitor.peak_for_impact(100.0).fraction_of_full_scale == pytest.approx(1.0)

    def test_a_railed_peak_is_flagged_as_clipped(self):
        monitor = self.build(clip_fraction=0.98)
        monitor.add_sample(3.3, timestamp=100.0)

        assert monitor.peak_for_impact(100.0).clipped is True

    def test_a_healthy_peak_is_not_flagged(self):
        monitor = self.build()
        monitor.add_sample(2.3, timestamp=100.0)

        peak = monitor.peak_for_impact(100.0)

        assert peak.clipped is False
        assert peak.fraction_of_full_scale == pytest.approx(0.697, rel=1e-2)

    def test_the_payload_rounds_for_the_ui(self):
        monitor = self.build()
        monitor.add_sample(2.34567, timestamp=100.0)

        payload = monitor.peak_for_impact(100.0).to_dict()

        assert payload["volts"] == pytest.approx(2.3457)
        assert payload["clipped"] is False

    def test_a_nonsense_full_scale_is_refused(self):
        with pytest.raises(ValueError):
            EnvelopeMonitor(MockADS1115(), full_scale_volts=0)

    def test_history_is_bounded(self):
        monitor = EnvelopeMonitor(MockADS1115(), full_scale_volts=3.3, history_s=0.01)

        for index in range(5000):
            monitor.add_sample(1.0, timestamp=100.0 + index)

        assert len(monitor._samples) < 5000  # pylint: disable=protected-access


class TestLatestSample:
    def test_it_reports_the_most_recent_sample(self):
        monitor = EnvelopeMonitor(MockADS1115(), full_scale_volts=3.3)
        monitor.add_sample(0.4, timestamp=1.0)
        monitor.add_sample(2.31, timestamp=2.0)

        latest = monitor.latest_sample()

        assert latest.volts == pytest.approx(2.31)
        assert latest.fraction_of_full_scale == pytest.approx(0.7, rel=1e-2)
        assert latest.clipped is False

    def test_an_empty_buffer_reports_nothing(self):
        assert EnvelopeMonitor(MockADS1115(), full_scale_volts=3.3).latest_sample() is None

    def test_a_railed_live_sample_is_flagged(self):
        monitor = EnvelopeMonitor(MockADS1115(), full_scale_volts=3.3, clip_fraction=0.98)
        monitor.add_sample(3.3, timestamp=1.0)

        assert monitor.latest_sample().clipped is True


class TestSamplingThread:
    def test_start_and_stop_drive_the_adc_lifecycle(self):
        adc = MockADS1115(volts=1.5)
        monitor = EnvelopeMonitor(adc, full_scale_volts=3.3, sample_interval_s=0.001)

        monitor.start()
        try:
            deadline = __import__("time").time() + 1.0
            while monitor.peak_for_impact(__import__("time").time()) is None:
                if __import__("time").time() > deadline:
                    pytest.fail("sampling thread produced nothing")
        finally:
            monitor.stop()

        assert adc.is_open is False

    def test_a_read_failure_is_recorded_without_killing_the_thread(self):
        class BrokenADC(MockADS1115):
            def read_volts(self):
                raise OSError("i2c read failed")

        monitor = EnvelopeMonitor(BrokenADC(), full_scale_volts=3.3, sample_interval_s=0.001)
        monitor.start()
        try:
            deadline = __import__("time").time() + 1.0
            while monitor.last_error is None and __import__("time").time() < deadline:
                pass
        finally:
            monitor.stop()

        assert monitor.last_error == "i2c read failed"
