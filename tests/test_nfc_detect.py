"""Tests for auto-detecting which reader chip is wired up."""

import pytest

from openflight.nfc.detect import DETECT_ORDER, build_reader, detect_reader
from openflight.nfc.reader import NfcReaderError


class FakeReader:
    """A reader that either answers its open() probe or does not."""

    def __init__(self, name, *, answers=True):
        self.name = name
        self.answers = answers
        self.opened = False
        self.closed = False

    def open(self):
        self.opened = True
        if not self.answers:
            raise NfcReaderError(f"{self.name} did not answer")

    def read_tag(self, timeout_s=0.5):
        return None

    def close(self):
        self.closed = True


def _factory(**answers):
    """Build a probe factory whose chips answer per the given flags."""
    built = {}

    def make(chip):
        built[chip] = FakeReader(chip, answers=answers.get(chip, False))
        return built[chip]

    make.built = built  # type: ignore[attr-defined]
    return make


class TestDetectOrder:
    def test_the_pn5180_is_probed_before_the_pn532(self):
        # Its probe is a passive EEPROM read; the PN532's writes a frame and
        # waits on an ACK, which is the slower way to learn nothing.
        assert DETECT_ORDER == ("pn5180", "pn532")

    def test_a_pn5180_that_answers_is_returned_without_probing_further(self):
        factory = _factory(pn5180=True, pn532=True)

        reader = detect_reader(factory=factory)

        assert reader.name == "pn5180"
        assert "pn532" not in factory.built

    def test_a_pn532_is_found_when_the_pn5180_stays_silent(self):
        factory = _factory(pn5180=False, pn532=True)

        reader = detect_reader(factory=factory)

        assert reader.name == "pn532"

    def test_the_detected_reader_is_returned_already_open(self):
        # Opening it is how it was identified; opening again resets the chip.
        factory = _factory(pn5180=True)

        reader = detect_reader(factory=factory)

        assert reader.opened is True

    def test_a_chip_that_did_not_answer_is_closed_again(self):
        factory = _factory(pn5180=False, pn532=True)

        detect_reader(factory=factory)

        assert factory.built["pn5180"].closed is True

    def test_nothing_attached_raises_with_every_chip_s_reason(self):
        factory = _factory()

        with pytest.raises(NfcReaderError) as raised:
            detect_reader(factory=factory)

        message = str(raised.value)
        assert "pn5180: pn5180 did not answer" in message
        assert "pn532: pn532 did not answer" in message

    def test_a_constructor_that_explodes_is_treated_as_absent(self):
        def make(chip):
            if chip == "pn5180":
                raise OSError("No such file or directory: /dev/spidev0.0")
            return FakeReader(chip)

        assert detect_reader(factory=make).name == "pn532"

    def test_the_probe_order_can_be_narrowed(self):
        factory = _factory(pn5180=True, pn532=True)

        reader = detect_reader(order=("pn532",), factory=factory)

        assert reader.name == "pn532"


class TestBuildReader:
    def test_pn5180_settings_are_forwarded(self):
        reader = build_reader("pn5180", spi_bus=1, spi_device=2, busy_gpio=27, reset_gpio=17)

        assert (reader.spi_bus, reader.spi_device) == (1, 2)
        assert (reader.busy_gpio, reader.reset_gpio) == (27, 17)

    def test_pn532_settings_are_forwarded(self):
        reader = build_reader("pn532", interface="i2c", bus_number=3, address=0x25)

        assert reader.interface == "i2c"
        assert (reader.bus_number, reader.address) == (3, 0x25)

    def test_each_driver_only_sees_options_it_understands(self):
        # One chip-agnostic settings dict feeds both drivers; the PN5180 must
        # not choke on the PN532's I2C keys, or vice versa.
        settings = {
            "interface": "spi",
            "spi_bus": 0,
            "spi_device": 0,
            "irq_gpio": 22,
            "bus_number": 1,
            "address": 0x24,
            "busy_gpio": 23,
            "reset_gpio": 24,
        }

        assert build_reader("pn5180", **settings).name == "pn5180"
        assert build_reader("pn532", **settings).name == "pn532"

    def test_an_unknown_chip_is_refused(self):
        with pytest.raises(ValueError, match="Unknown NFC reader chip"):
            build_reader("rc522")
