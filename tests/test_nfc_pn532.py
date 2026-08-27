"""Tests for the PN532 I2C driver and its frame codec."""

import pytest

from openflight.nfc.pn532 import (
    ACK_FRAME,
    HOST_TO_PN532,
    PN532_TO_HOST,
    PN532I2C,
    PN532FrameError,
    build_frame,
    parse_frame,
    parse_passive_target_uid,
)
from openflight.nfc.reader import NfcReaderError


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


class FakeTransport:
    """Replays scripted reads and records writes.

    The driver reads one byte to poll readiness and a longer block to collect a
    frame, so the script is a list of byte strings served in order and sliced to
    whatever length the driver asks for.
    """

    def __init__(self, reads):
        self.reads = list(reads)
        self.writes = []
        self.closed = False

    def write(self, data):
        self.writes.append(bytes(data))

    def read(self, length):
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
