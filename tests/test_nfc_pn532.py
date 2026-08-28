"""Tests for the PN532 I2C driver and its frame codec."""

import errno

import pytest

from openflight.nfc import ndef
from openflight.nfc.pn532 import (
    ACK_FRAME,
    HOST_TO_PN532,
    PN532_TO_HOST,
    PN532I2C,
    PN532FrameError,
    SPI_DATAREAD,
    SPI_DATAWRITE,
    SPI_STATREAD,
    SpiTransport,
    build_frame,
    disable_kernel_chip_select,
    parse_frame,
    parse_frame,
    parse_passive_target,
    parse_passive_target_uid,
    reverse_byte,
)
from openflight.nfc.reader import NfcReaderError, TagWriteError


def _response(command_echo: int, payload: bytes) -> bytes:
    """Build the bytes a PN532 would return for one command, status byte first."""
    frame = build_frame(bytes([PN532_TO_HOST, command_echo]) + payload)
    # Real reads are fixed-length, so pad like the bus does.
    return bytes([0x01]) + frame + bytes(64)


FIRMWARE_RESPONSE = _response(0x03, bytes([0x32, 0x01, 0x06, 0x07]))
RF_CONFIG_RESPONSE = _response(0x33, b"")
SAM_RESPONSE = _response(0x15, bytes([0x15]))
NO_TAG_RESPONSE = _response(0x4B, bytes([0x00]))
TAG_RESPONSE = _response(0x4B, bytes([0x01, 0x01, 0x00, 0x04, 0x08, 0x04, 0x04, 0xA2, 0xB1, 0xC3]))
ACK_READ = bytes([0x01]) + ACK_FRAME

# SEL_RES (SAK) 0x00 is an NFC Forum Type 2 tag; 0x08 is a MIFARE Classic 1K.
TYPE2_TAG_RESPONSE = _response(
    0x4B, bytes([0x01, 0x01, 0x00, 0x44, 0x00, 0x04, 0x04, 0xA2, 0xB1, 0xC3])
)
CLASSIC_TAG_RESPONSE = TAG_RESPONSE
OTHER_TYPE2_TAG_RESPONSE = _response(
    0x4B, bytes([0x01, 0x01, 0x00, 0x44, 0x00, 0x04, 0x04, 0xA2, 0xB1, 0xFF])
)
CAPABILITY_CONTAINER = bytes([0xE1, 0x10, 0x12, 0x00])


def _exchange_response(data: bytes) -> bytes:
    """An InDataExchange answer carrying a success status and this data."""
    return _response(0x41, bytes([0x00]) + data)


def _memory_reads(memory: bytes) -> list[bytes]:
    """The reads a Type 2 tag serves for one read_tag: the CC, then user memory.

    A Type 2 READ returns 16 bytes from the requested page, so reading the
    capability container at page 3 also returns the first three data pages.
    """
    padded = memory.ljust(64, b"\x00")
    reads = [_exchange_response(CAPABILITY_CONTAINER + padded[:12])]
    for offset in range(0, 64, 16):
        reads.append(_exchange_response(padded[offset : offset + 16]))
    return reads


def _interleave_acks(responses):
    """Every command is answered with an ACK frame and then its response."""
    return [item for response in responses for item in (ACK_READ, response)]


class FakeTransport:
    """Replays scripted reads and records writes.

    The driver reads one byte to poll readiness and a longer block to collect a
    frame, so the script is a list of byte strings served in order and sliced to
    whatever length the driver asks for.
    """

    def __init__(self, reads, *, nack_polls: int = 0, poll_timeouts: int = 0):
        self.reads = list(reads)
        self.writes = []
        self.closed = False
        # Real silicon NACKs I2C reads until a frame is waiting (Linux errno 121).
        self.nack_polls = nack_polls
        # The Pi I2C controller times out (errno 110) when the PN532 stretches SCL.
        self.poll_timeouts = poll_timeouts

    def write(self, data):
        self.writes.append(bytes(data))

    def read(self, length):
        if length == 1 and self.poll_timeouts:
            self.poll_timeouts -= 1
            raise TimeoutError(110, "Connection timed out")
        if length == 1 and self.nack_polls:
            self.nack_polls -= 1
            raise OSError(121, "Remote I/O error")
        if not self.reads:
            raise AssertionError("PN532 driver read past the end of the script")
        chunk = self.reads[0]
        if length == 1:
            # A readiness poll consumes nothing; the frame read that follows does.
            return chunk[:1]
        self.reads.pop(0)
        return chunk[:length].ljust(length, b"\x00")

    def close(self):
        self.closed = True


def _open_reader(*extra_reads):
    """Return a reader that has completed its open() handshake."""
    transport = FakeTransport(
        [
            ACK_READ,
            FIRMWARE_RESPONSE,
            ACK_READ,
            RF_CONFIG_RESPONSE,
            ACK_READ,
            SAM_RESPONSE,
            *extra_reads,
        ]
    )
    reader = PN532I2C(transport=transport)
    reader.open()
    return reader, transport


class TestFrameCodec:
    def test_round_trip(self):
        payload = bytes([HOST_TO_PN532, 0x4A, 0x01, 0x00])

        assert parse_frame(build_frame(payload)) == payload

    def test_leading_and_trailing_noise_is_tolerated(self):
        payload = bytes([HOST_TO_PN532, 0x02])
        noisy = b"\x00\x00\x00" + build_frame(payload) + b"\xff\xff"

        assert parse_frame(noisy) == payload

    def test_length_checksum_is_verified(self):
        frame = bytearray(build_frame(bytes([HOST_TO_PN532, 0x02])))
        frame[4] ^= 0xFF

        with pytest.raises(PN532FrameError, match="length checksum"):
            parse_frame(bytes(frame))

    def test_data_checksum_is_verified(self):
        frame = bytearray(build_frame(bytes([HOST_TO_PN532, 0x02])))
        frame[-2] ^= 0xFF

        with pytest.raises(PN532FrameError, match="data checksum"):
            parse_frame(bytes(frame))

    def test_a_missing_start_code_is_an_error(self):
        with pytest.raises(PN532FrameError, match="No start code"):
            parse_frame(b"\x11\x22\x33")

    def test_a_truncated_frame_is_an_error(self):
        with pytest.raises(PN532FrameError):
            parse_frame(build_frame(bytes([HOST_TO_PN532, 0x02]))[:6])

    def test_an_oversized_payload_is_refused(self):
        with pytest.raises(PN532FrameError):
            build_frame(bytes(255))

    def test_an_empty_payload_is_refused(self):
        with pytest.raises(PN532FrameError):
            build_frame(b"")


class TestTargetParsing:
    def test_no_target_is_not_an_error(self):
        assert parse_passive_target_uid(bytes([0x00])) is None

    def test_a_four_byte_uid_is_normalized(self):
        payload = bytes([0x01, 0x01, 0x00, 0x04, 0x08, 0x04, 0x04, 0xA2, 0xB1, 0xC3])

        assert parse_passive_target_uid(payload) == "04A2B1C3"

    def test_a_seven_byte_uid_is_read_in_full(self):
        uid = [0x04, 0xA2, 0xB1, 0xC3, 0xD4, 0xE5, 0xF6]
        payload = bytes([0x01, 0x01, 0x00, 0x44, 0x00, 0x07, *uid])

        assert parse_passive_target_uid(payload) == "04A2B1C3D4E5F6"

    def test_a_truncated_descriptor_is_an_error(self):
        with pytest.raises(PN532FrameError):
            parse_passive_target_uid(bytes([0x01, 0x01]))

    def test_a_uid_shorter_than_declared_is_an_error(self):
        with pytest.raises(PN532FrameError):
            parse_passive_target_uid(bytes([0x01, 0x01, 0x00, 0x04, 0x08, 0x07, 0x04]))

    def test_an_empty_payload_is_an_error(self):
        with pytest.raises(PN532FrameError):
            parse_passive_target_uid(b"")


class TestReaderLifecycle:
    def test_open_reads_firmware_and_configures_the_reader(self):
        reader, transport = _open_reader()

        assert reader.firmware_version == "1.6"
        # GetFirmwareVersion, RFConfiguration, SAMConfiguration.
        assert [write[6] for write in transport.writes] == [0x02, 0x32, 0x14]

    def test_open_rejects_a_device_that_is_not_a_pn532(self):
        transport = FakeTransport([ACK_READ, _response(0x03, bytes([0x99, 0x01, 0x06, 0x07]))])
        reader = PN532I2C(transport=transport)

        with pytest.raises(NfcReaderError, match="not a PN532"):
            reader.open()
        assert transport.closed is True

    def test_a_missing_ack_fails_the_open(self):
        transport = FakeTransport([bytes([0x01]) + b"\x00\x00\xff\x01\xff\x00"])
        reader = PN532I2C(transport=transport)

        with pytest.raises(PN532FrameError, match="Expected ACK"):
            reader.open()

    def test_open_retries_when_the_pn532_nacks_until_ready(self):
        # The PN532 NACKs I2C status polls while it is busy. That is "not ready",
        # not a dead bus: i2cdetect still shows 0x24, and the next poll succeeds.
        transport = FakeTransport(
            [ACK_READ, FIRMWARE_RESPONSE, ACK_READ, RF_CONFIG_RESPONSE, ACK_READ, SAM_RESPONSE],
            nack_polls=3,
        )
        reader = PN532I2C(transport=transport)

        reader.open()

        assert reader.firmware_version == "1.6"
        assert transport.nack_polls == 0

    def test_a_reader_that_never_becomes_ready_times_out(self):
        transport = FakeTransport([], nack_polls=10**9)
        reader = PN532I2C(transport=transport, ack_timeout_s=0.05)

        with pytest.raises(NfcReaderError, match="SPI row"):
            reader.open()
        assert transport.closed is True

    def test_i2c_ack_timeout_mentions_the_i2c_row(self):
        transport = FakeTransport([], nack_polls=10**9)
        reader = PN532I2C(transport=transport, interface="i2c", ack_timeout_s=0.05)

        with pytest.raises(NfcReaderError, match="I2C row"):
            reader.open()

    def test_a_status_poll_timeout_is_not_swallowed_as_a_nack(self):
        # Errno 110 is the Pi aborting clock-stretch, not the PN532 saying
        # "not ready". Treating it as a NACK hides the real failure behind
        # "did not acknowledge the command".
        transport = FakeTransport([], poll_timeouts=10**9)
        reader = PN532I2C(transport=transport, ack_timeout_s=0.05)

        with pytest.raises(NfcReaderError, match="timed out"):
            reader.open()
        assert transport.closed is True

    def test_reading_before_open_is_an_error(self):
        with pytest.raises(NfcReaderError, match="not open"):
            PN532I2C(transport=FakeTransport([])).read_uid()

    def test_close_releases_the_bus_once(self):
        reader, transport = _open_reader()

        reader.close()
        reader.close()

        assert transport.closed is True


class TestReadUid:
    def test_an_empty_field_returns_none(self):
        reader, _ = _open_reader(ACK_READ, NO_TAG_RESPONSE)

        assert reader.read_uid() is None

    def test_a_present_tag_returns_its_uid(self):
        reader, _ = _open_reader(ACK_READ, TAG_RESPONSE)

        assert reader.read_uid() == "04A2B1C3"

    def test_an_answer_to_the_wrong_command_is_rejected(self):
        reader, _ = _open_reader(ACK_READ, _response(0x03, bytes([0x32, 0x01, 0x06, 0x07])))

        with pytest.raises(PN532FrameError, match="Unexpected answer"):
            reader.read_uid()

    def test_a_silent_reader_times_out(self):
        reader, transport = _open_reader()
        transport.reads = [ACK_READ, bytes([0x00]) * 64]

        with pytest.raises(NfcReaderError, match="did not answer"):
            reader.read_uid(timeout_s=0.05)


CLUB_MEMORY = ndef.wrap_tlv(ndef.encode_text_record("7-iron"))


class TestReadTag:
    def test_a_type_2_tag_reports_the_club_written_on_it(self):
        reader, _ = _open_reader(
            ACK_READ, TYPE2_TAG_RESPONSE, *_interleave_acks(_memory_reads(CLUB_MEMORY))
        )

        tag = reader.read_tag()

        assert tag.uid == "04A2B1C3"
        assert tag.text == "7-iron"
        assert tag.writable is True
        assert tag.blank is False

    def test_a_factory_fresh_type_2_tag_reads_as_blank_and_writable(self):
        reader, _ = _open_reader(
            ACK_READ, TYPE2_TAG_RESPONSE, *_interleave_acks(_memory_reads(bytes(64)))
        )

        tag = reader.read_tag()

        assert tag.blank is True
        assert tag.writable is True
        assert tag.text is None

    def test_a_mifare_classic_card_is_read_by_uid_only(self):
        # No memory reads are scripted: attempting one would run off the script.
        reader, _ = _open_reader(ACK_READ, CLASSIC_TAG_RESPONSE)

        tag = reader.read_tag()

        assert tag.uid == "04A2B1C3"
        assert tag.writable is False
        assert tag.blank is False

    def test_a_tag_without_the_ndef_capability_container_is_not_written(self):
        reader, _ = _open_reader(
            ACK_READ,
            TYPE2_TAG_RESPONSE,
            ACK_READ,
            _exchange_response(bytes(16)),
        )

        tag = reader.read_tag()

        assert tag.writable is False
        assert tag.text is None

    def test_a_tag_lifted_away_mid_read_falls_back_to_uid_only(self):
        reader, _ = _open_reader(
            ACK_READ,
            TYPE2_TAG_RESPONSE,
            ACK_READ,
            _response(0x41, bytes([0x13])),  # status: card not responding
        )

        tag = reader.read_tag()

        assert tag.uid == "04A2B1C3"
        assert tag.writable is False

    def test_an_empty_field_returns_nothing(self):
        reader, _ = _open_reader(ACK_READ, NO_TAG_RESPONSE)

        assert reader.read_tag() is None


class TestWriteText:
    def _write_script(self, memory_after):
        """Poll, four page writes, then the read-back."""
        page_writes = [_exchange_response(b"")] * len(
            range(0, len(ndef.wrap_tlv(ndef.encode_text_record("7-iron"))), 4)
        )
        return [
            ACK_READ,
            TYPE2_TAG_RESPONSE,
            *_interleave_acks(page_writes),
            *_interleave_acks(_memory_reads(memory_after)),
        ]

    def test_writing_a_club_verifies_it_by_reading_back(self):
        reader, transport = _open_reader(*self._write_script(CLUB_MEMORY))

        reader.write_text("04A2B1C3", "7-iron")

        # Page writes start at page 4, the first user-memory page.
        writes = [write for write in transport.writes if write[6] == 0x40 and write[8] == 0xA2]
        assert [write[9] for write in writes] == [4, 5, 6, 7]

    def test_a_write_that_does_not_read_back_is_a_failure(self):
        reader, _ = _open_reader(*self._write_script(bytes(64)))

        with pytest.raises(TagWriteError, match="did not read back"):
            reader.write_text("04A2B1C3", "7-iron")

    def test_writing_refuses_when_a_different_tag_is_on_the_reader(self):
        # A club was swapped onto the reader between confirming and writing.
        reader, _ = _open_reader(ACK_READ, OTHER_TYPE2_TAG_RESPONSE)

        with pytest.raises(TagWriteError, match="not on the reader"):
            reader.write_text("04A2B1C3", "7-iron", timeout_s=0.0)

    def test_writing_refuses_when_no_tag_is_on_the_reader(self):
        reader, _ = _open_reader(ACK_READ, NO_TAG_RESPONSE)

        with pytest.raises(TagWriteError, match="not on the reader"):
            reader.write_text("04A2B1C3", "7-iron", timeout_s=0.0)

    def test_writing_waits_for_the_tag_to_come_back_to_the_reader(self):
        reader, _ = _open_reader(
            ACK_READ,
            NO_TAG_RESPONSE,
            ACK_READ,
            TYPE2_TAG_RESPONSE,
            *_interleave_acks([_exchange_response(b"")] * 4),
            *_interleave_acks(_memory_reads(CLUB_MEMORY)),
        )

        reader.write_text("04A2B1C3", "7-iron", timeout_s=2.0)

    def test_writing_refuses_a_tag_type_it_cannot_write(self):
        reader, _ = _open_reader(ACK_READ, CLASSIC_TAG_RESPONSE)

        with pytest.raises(TagWriteError, match="cannot be written"):
            reader.write_text("04A2B1C3", "7-iron", timeout_s=0.01)

    def test_a_refused_page_write_is_reported_with_its_page(self):
        reader, _ = _open_reader(
            ACK_READ,
            TYPE2_TAG_RESPONSE,
            ACK_READ,
            _response(0x41, bytes([0x13])),
        )

        with pytest.raises(TagWriteError, match="page 4"):
            reader.write_text("04A2B1C3", "7-iron")


class TestTagTypeDetection:
    def test_the_sak_is_reported_alongside_the_uid(self):
        payload = bytes([0x01, 0x01, 0x00, 0x44, 0x00, 0x04, 0x04, 0xA2, 0xB1, 0xC3])

        assert parse_passive_target(payload) == ("04A2B1C3", 0x00)

    def test_a_mifare_classic_sak_is_preserved(self):
        payload = bytes([0x01, 0x01, 0x00, 0x04, 0x08, 0x04, 0x04, 0xA2, 0xB1, 0xC3])

        assert parse_passive_target(payload) == ("04A2B1C3", 0x08)

    def test_no_target_reports_nothing(self):
        assert parse_passive_target(bytes([0x00])) is None


class _FakeSpi:
    def __init__(self, replies=None):
        self.xfers = []
        self._replies = list(replies or [])

    def xfer2(self, data):
        self.xfers.append(list(data))
        if self._replies:
            reply = self._replies.pop(0)
            padded = list(reply) + [0] * max(0, len(data) - len(reply))
            return padded[: len(data)]
        return [0] * len(data)

    def close(self):
        pass


class _FakeCs:
    def __init__(self):
        self.events = []

    def on(self):
        self.events.append("select")

    def off(self):
        self.events.append("release")

    def close(self):
        pass


class _FakeIrq:
    def __init__(self, active=False):
        self.is_active = active

    def close(self):
        pass


class TestSpiTransport:
    def test_kernel_cs_disable_ignores_einval(self):
        class _Spi:
            @property
            def no_cs(self):
                return False

            @no_cs.setter
            def no_cs(self, _value):
                raise OSError(errno.EINVAL, "Invalid argument")

        disable_kernel_chip_select(_Spi())

    def test_kernel_cs_disable_reraises_other_oserrors(self):
        class _Spi:
            @property
            def no_cs(self):
                return False

            @no_cs.setter
            def no_cs(self, _value):
                raise OSError(errno.EIO, "I/O error")

        with pytest.raises(OSError) as raised:
            disable_kernel_chip_select(_Spi())
        assert raised.value.errno == errno.EIO
    def test_reverse_byte_swaps_lsb_and_msb(self):
        assert reverse_byte(0x01) == 0x80
        assert reverse_byte(0x80) == 0x01
        assert reverse_byte(0x00) == 0x00
        assert reverse_byte(0xFF) == 0xFF
        assert reverse_byte(reverse_byte(0xD4)) == 0xD4

    def test_wakeup_without_gpio_cs_clocks_dummy_bytes(self):
        spi = _FakeSpi()
        transport = SpiTransport(spi=spi, irq=_FakeIrq(active=True))

        transport.wakeup()

        assert len(spi.xfers) == 1
        assert len(spi.xfers[0]) == 1

    def test_injected_spi_does_not_claim_ce0(self):
        transport = SpiTransport(spi=_FakeSpi(), irq=_FakeIrq(active=True))

        assert transport._cs is None

    def test_wakeup_holds_nss_low_then_releases(self):
        cs = _FakeCs()
        spi = _FakeSpi()
        transport = SpiTransport(spi=spi, irq=_FakeIrq(active=True), cs=cs)

        transport.wakeup()

        assert cs.events == ["select", "release"]
        assert spi.xfers == []

    def test_write_selects_nss_before_clocking(self):
        cs = _FakeCs()
        transport = SpiTransport(spi=_FakeSpi(), irq=_FakeIrq(active=True), cs=cs)

        transport.write(bytes([HOST_TO_PN532, 0x02]))

        assert cs.events[0] == "select"
        assert cs.events[-1] == "release"

    def test_write_prefixes_datawrite_and_bit_reverses(self):
        spi = _FakeSpi()
        transport = SpiTransport(spi=spi, irq=_FakeIrq(active=True))

        transport.write(bytes([HOST_TO_PN532, 0x02]))

        assert spi.xfers[0][0] == reverse_byte(SPI_DATAWRITE)
        assert [reverse_byte(byte) for byte in spi.xfers[0][1:]] == [HOST_TO_PN532, 0x02]

    def test_a_one_byte_read_is_the_irq_ready_bit(self):
        spi = _FakeSpi()
        transport = SpiTransport(spi=spi, irq=_FakeIrq(active=True))

        assert transport.read(1) == bytes([0x01])
        assert spi.xfers == []

    def test_probe_status_returns_decoded_and_raw_bytes(self):
        spi = _FakeSpi(replies=[[0, reverse_byte(0x01)]])
        transport = SpiTransport(spi=spi, irq=_FakeIrq(active=False))

        status, raw = transport.probe_status()

        assert status == 0x01
        assert raw[1] == reverse_byte(0x01)
        assert spi.xfers[0][0] == reverse_byte(SPI_STATREAD)

    def test_ready_falls_back_to_statread_when_irq_is_idle(self):
        spi = _FakeSpi(replies=[[0, reverse_byte(0x01)]])
        transport = SpiTransport(spi=spi, irq=_FakeIrq(active=False))

        assert transport.read(1) == bytes([0x01])
        assert spi.xfers[0][0] == reverse_byte(SPI_STATREAD)

    def test_idle_irq_and_busy_status_is_not_ready(self):
        transport = SpiTransport(spi=_FakeSpi(), irq=_FakeIrq(active=False))

        assert transport.read(1) == bytes([0x00])

    def test_a_frame_read_uses_dataread_and_prepends_status(self):
        spi = _FakeSpi()
        transport = SpiTransport(spi=spi, irq=_FakeIrq(active=True))

        frame = transport.read(3)

        assert frame[0] == 0x01
        assert spi.xfers[0][0] == reverse_byte(SPI_DATAREAD)
        assert len(spi.xfers[0]) == 3

    def test_an_unknown_interface_is_rejected(self):
        with pytest.raises(ValueError, match="spi"):
            PN532I2C(interface="uart")

