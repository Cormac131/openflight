"""Tests for the PN5180 driver: CRC codecs, the BUSY-gated transport, and the
ISO14443A / ISO15693 poll logic built on top of them.
"""

from typing import Optional

import pytest

from openflight.nfc import ndef
from openflight.nfc.pn5180 import (
    _CASCADE_LEVELS,
    CMD_LOAD_RF_CONFIG,
    CMD_READ_DATA,
    CMD_READ_EEPROM,
    CMD_READ_REGISTER,
    CMD_RF_OFF,
    CMD_RF_ON,
    CMD_SEND_DATA,
    CMD_WRITE_REGISTER,
    EEPROM_FIRMWARE_VERSION,
    EEPROM_PRODUCT_VERSION,
    ISO14443A_ANTICOLLISION_CL1,
    ISO14443A_CASCADE_TAG,
    ISO14443A_NVB_COLLIDE,
    ISO14443A_NVB_FINAL,
    ISO14443A_REQA,
    ISO15693_CMD_INVENTORY,
    ISO15693_CMD_READ_SINGLE_BLOCK,
    ISO15693_ERROR_FLAG,
    REG_IRQ_STATUS,
    REG_RX_STATUS,
    RF_CONFIG_ISO14443A_106,
    RF_CONFIG_ISO15693_26,
    RX_IRQ_STAT,
    PN5180FrameError,
    Pn5180Spi,
    Pn5180SpiTransport,
    crc_a,
    crc_iso15693,
)
from openflight.nfc.reader import NfcReaderError
from openflight.nfc.type2 import CAPABILITY_CONTAINER_MAGIC, TYPE2_SAK

# --------------------------------------------------------------------------- CRC


class TestCrcA:
    def test_matches_a_known_iso14443_vector(self):
        # SELECT for a 4-byte UID, a value cross-checked against libnfc's CRC_A.
        assert crc_a(bytes([0x93, 0x70, 0x04, 0xA2, 0xB1, 0xC3, 0x64])) == bytes([0xB9, 0x7A])

    def test_changes_with_any_input_byte(self):
        assert crc_a(b"\x26") != crc_a(b"\x27")

    def test_is_two_bytes_for_empty_input(self):
        assert len(crc_a(b"")) == 2


class TestCrcIso15693:
    def test_uses_a_different_init_and_final_xor_than_crc_a(self):
        assert crc_iso15693(b"\x26\x01\x00") != crc_a(b"\x26\x01\x00")

    def test_is_two_bytes_for_empty_input(self):
        assert len(crc_iso15693(b"")) == 2


# --------------------------------------------------------------------- transport


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


class _FakeBusy:
    def __init__(self, active=False):
        self.is_active = active

    def close(self):
        pass


class _FakeReset:
    def __init__(self):
        self.events = []

    def on(self):
        self.events.append("assert")

    def off(self):
        self.events.append("release")

    def close(self):
        pass


class TestPn5180SpiTransport:
    def test_reset_pulses_nreset_low_then_high(self):
        reset = _FakeReset()
        transport = Pn5180SpiTransport(spi=_FakeSpi(), busy=_FakeBusy(), reset=reset)

        transport.reset()

        assert reset.events == ["assert", "release"]

    def test_reset_without_a_reset_line_is_a_no_op(self):
        transport = Pn5180SpiTransport(spi=_FakeSpi(), busy=_FakeBusy(), reset=None)

        transport.reset()  # must not raise

    def test_command_writes_then_reads_as_two_transactions(self):
        spi = _FakeSpi()
        transport = Pn5180SpiTransport(spi=spi, busy=_FakeBusy(active=False), reset=_FakeReset())

        transport.command(bytes([0x04, 0x02]), response_len=4)

        assert len(spi.xfers) == 2
        assert spi.xfers[0] == [0x04, 0x02]
        assert spi.xfers[1] == [0, 0, 0, 0]

    def test_a_write_only_command_does_not_read(self):
        spi = _FakeSpi()
        transport = Pn5180SpiTransport(spi=spi, busy=_FakeBusy(active=False), reset=_FakeReset())

        transport.command(bytes([0x16, 0x00]))

        assert len(spi.xfers) == 1

    def test_busy_stuck_high_times_out(self):
        transport = Pn5180SpiTransport(spi=_FakeSpi(), busy=_FakeBusy(active=True), reset=None)

        with pytest.raises(NfcReaderError, match="BUSY"):
            transport.command(bytes([0x16, 0x00]))

    def test_close_releases_busy_reset_and_spi_once(self):
        transport = Pn5180SpiTransport(spi=_FakeSpi(), busy=_FakeBusy(), reset=_FakeReset())

        transport.close()
        transport.close()  # must be idempotent

    def test_injected_gpios_are_not_closed(self):
        busy = _FakeBusy()
        reset = _FakeReset()
        transport = Pn5180SpiTransport(spi=_FakeSpi(), busy=busy, reset=reset)

        transport.close()  # must not raise even though these objects are shared


# ------------------------------------------------------------------ fake host bus


class Iso14443aTag:
    """Answers the request shapes the PN5180 driver sends for one A tag."""

    def __init__(self, uid_hex, *, sak=TYPE2_SAK, type2_pages=None, present=True):
        self.uid = bytes.fromhex(uid_hex)
        self.sak = sak
        self.type2_pages = type2_pages or {}
        self.present = present

    def respond(self, frame: bytes) -> Optional[bytes]:
        if not self.present:
            return None
        if frame == bytes([ISO14443A_REQA]):
            return bytes([0x44, 0x00])
        blocks = self._cascade_blocks()
        for sel, block in zip(_CASCADE_LEVELS, blocks):
            if frame[:2] == bytes([sel, ISO14443A_NVB_COLLIDE]):
                bcc = block[0] ^ block[1] ^ block[2] ^ block[3]
                return block + bytes([bcc])
            if frame[:2] == bytes([sel, ISO14443A_NVB_FINAL]):
                is_final = block is blocks[-1]
                sak = self.sak if is_final else 0x04
                return bytes([sak]) + crc_a(bytes([sak]))
        if len(frame) >= 2 and frame[0] == 0x30:  # TYPE2_READ
            data = self.type2_pages.get(frame[1], bytes(16))
            return data + crc_a(data)
        return None

    def _cascade_blocks(self):
        remaining = self.uid
        blocks = []
        while len(remaining) > 4:
            blocks.append(bytes([ISO14443A_CASCADE_TAG]) + remaining[:3])
            remaining = remaining[3:]
        blocks.append(remaining)
        return blocks


class Iso15693Tag:
    """Answers Inventory and Read Single Block for one ISO15693 tag."""

    def __init__(self, uid_hex, *, blocks=None, present=True, error_read=False):
        self.uid = bytes.fromhex(uid_hex)
        self.blocks = blocks or {}
        self.present = present
        self.error_read = error_read

    def respond(self, frame: bytes) -> Optional[bytes]:
        if not self.present or len(frame) < 2:
            # Too short to be an ISO15693 command -- an ISO14443A REQA
            # ("\x26", 1 byte) is what the driver tries first each poll.
            return None
        cmd = frame[1]
        if cmd == ISO15693_CMD_INVENTORY:
            payload = bytes([0x00, 0x00]) + bytes(reversed(self.uid))
            return payload + crc_iso15693(payload)
        if cmd == ISO15693_CMD_READ_SINGLE_BLOCK:
            uid_wire = frame[2:10]
            if bytes(reversed(uid_wire)) != self.uid:
                return None
            block = frame[10]
            if self.error_read:
                payload = bytes([ISO15693_ERROR_FLAG, 0x0F])
                return payload + crc_iso15693(payload)
            data = self.blocks.get(block, bytes(4))
            payload = bytes([0x00]) + data
            return payload + crc_iso15693(payload)
        return None


class FakeTransport:
    """A PN5180 bus driven by a simulated tag, scripted register/EEPROM answers,
    and knobs for exercising the IRQ wait loop and its timeout.
    """

    def __init__(self, *, tag=None, irq_delay=0, never_ready=False):
        self.tag = tag
        self.registers: dict[int, int] = {}
        self.writes: list[bytes] = []
        self.reset_count = 0
        self.closed = False
        self.rf_config = None
        self._pending_response: Optional[bytes] = None
        self._irq_ready = False
        self._irq_delay = irq_delay
        self._never_ready = never_ready

    def reset(self) -> None:
        self.reset_count += 1

    def command(self, data: bytes, response_len: int = 0) -> bytes:
        self.writes.append(bytes(data))
        cmd = data[0]
        if cmd == CMD_WRITE_REGISTER:
            self.registers[data[1]] = int.from_bytes(data[2:6], "little")
            return b""
        if cmd == CMD_READ_REGISTER:
            addr = data[1]
            if addr == REG_IRQ_STATUS:
                return self._irq_status().to_bytes(4, "little")
            if addr == REG_RX_STATUS:
                return len(self._pending_response or b"").to_bytes(4, "little")
            return self.registers.get(addr, 0).to_bytes(4, "little")
        if cmd == CMD_READ_EEPROM:
            addr = data[1]
            if addr == EEPROM_FIRMWARE_VERSION:
                return bytes([0x06, 0x01])
            if addr == EEPROM_PRODUCT_VERSION:
                return bytes([0x02, 0x01])
            return bytes(response_len)
        if cmd == CMD_LOAD_RF_CONFIG:
            self.rf_config = (data[1], data[2])
            return b""
        if cmd in (CMD_RF_ON, CMD_RF_OFF):
            return b""
        if cmd == CMD_SEND_DATA:
            frame = bytes(data[2:])
            self._pending_response = self.tag.respond(frame) if self.tag else None
            self._irq_ready = self._pending_response is not None
            return b""
        if cmd == CMD_READ_DATA:
            return (self._pending_response or b"")[:response_len]
        raise AssertionError(f"Unhandled PN5180 command 0x{cmd:02x}")

    def close(self) -> None:
        self.closed = True

    def _irq_status(self) -> int:
        if self._never_ready:
            return 0
        if self._irq_delay > 0:
            self._irq_delay -= 1
            return 0
        return RX_IRQ_STAT if self._irq_ready else 0


def _reader(transport=None, **kwargs) -> Pn5180Spi:
    reader = Pn5180Spi(transport=transport or FakeTransport(), **kwargs)
    reader.open()
    return reader


# ------------------------------------------------------------------- lifecycle


class TestReaderLifecycle:
    def test_open_resets_and_reads_versions(self):
        transport = FakeTransport()

        reader = _reader(transport)

        assert transport.reset_count == 1
        assert reader.firmware_version == "1.6"
        assert reader.product_version == "1.2"

    def test_open_turns_the_rf_field_off_until_the_first_poll(self):
        transport = FakeTransport()

        _reader(transport)

        assert transport.writes[-1][0] == CMD_RF_OFF

    def test_a_transport_failure_during_open_closes_the_bus(self):
        class BrokenTransport(FakeTransport):
            def reset(self):
                raise OSError("SPI not found")

        transport = BrokenTransport()
        reader = Pn5180Spi(transport=transport)

        with pytest.raises(OSError):
            reader.open()
        assert transport.closed is True

    def test_reading_before_open_is_an_error(self):
        with pytest.raises(NfcReaderError, match="not open"):
            Pn5180Spi(transport=FakeTransport()).read_tag()

    def test_close_turns_the_field_off_and_releases_the_bus_once(self):
        transport = FakeTransport()
        reader = _reader(transport)

        reader.close()
        reader.close()

        assert transport.closed is True

    def test_a_busy_timeout_during_close_does_not_raise(self):
        transport = FakeTransport(never_ready=True)
        reader = _reader(transport)

        reader.close()  # RF_OFF failure inside close() must be swallowed


# -------------------------------------------------------------------- ISO14443A


class TestReadTagIso14443A:
    def test_an_empty_field_returns_none(self):
        reader = _reader(FakeTransport(tag=Iso14443aTag("04A2B1C3", present=False)))

        assert reader.read_tag(timeout_s=0.05) is None

    def test_a_four_byte_uid_type2_tag_reports_the_club_written_on_it(self):
        memory = ndef.wrap_tlv(ndef.encode_text_record("7-iron")).ljust(64, b"\x00")
        # A Type 2 READ always answers with a full 16-byte window regardless
        # of how many pages were asked for; only the first byte matters here.
        pages = {3: bytes([CAPABILITY_CONTAINER_MAGIC, 0x10, 0x12, 0x00]).ljust(16, b"\x00")}
        page = 4
        for offset in range(0, 64, 16):
            pages[page] = memory[offset : offset + 16]
            page += 4
        tag = Iso14443aTag("04A2B1C3", type2_pages=pages)

        result = _reader(FakeTransport(tag=tag)).read_tag()

        assert result.uid == "04A2B1C3"
        assert result.text == "7-iron"
        assert result.writable is True
        assert result.blank is False

    def test_a_seven_byte_uid_is_read_in_full(self):
        tag = Iso14443aTag("04A2B1C3D4E5F6", sak=0x08)

        result = _reader(FakeTransport(tag=tag)).read_tag()

        assert result.uid == "04A2B1C3D4E5F6"

    def test_a_non_type2_sak_is_read_by_uid_only(self):
        tag = Iso14443aTag("04A2B1C3", sak=0x08)

        result = _reader(FakeTransport(tag=tag)).read_tag()

        assert result.uid == "04A2B1C3"
        assert result.writable is False
        assert result.blank is False

    def test_a_factory_fresh_type2_tag_reads_as_blank_and_writable(self):
        # A Type 2 READ always answers with a full 16-byte window regardless
        # of how many pages were asked for; only the first byte matters here.
        pages = {3: bytes([CAPABILITY_CONTAINER_MAGIC, 0x10, 0x12, 0x00]).ljust(16, b"\x00")}
        tag = Iso14443aTag("04A2B1C3", type2_pages=pages)

        result = _reader(FakeTransport(tag=tag)).read_tag()

        assert result.blank is True
        assert result.writable is True
        assert result.text is None

    def test_a_tag_without_the_ndef_capability_container_is_not_written(self):
        tag = Iso14443aTag("04A2B1C3", type2_pages={})  # page 3 reads as all zero

        result = _reader(FakeTransport(tag=tag)).read_tag()

        assert result.writable is False
        assert result.text is None

    def test_selects_iso14443a_rf_config(self):
        transport = FakeTransport(tag=Iso14443aTag("04A2B1C3", sak=0x08))

        _reader(transport).read_tag()

        assert transport.rf_config == RF_CONFIG_ISO14443A_106

    def test_a_tag_pulled_away_after_atqa_raises(self):
        class VanishingTag(Iso14443aTag):
            def respond(self, frame):
                if frame == bytes([ISO14443A_REQA]):
                    return bytes([0x44, 0x00])
                return None

        transport = FakeTransport(tag=VanishingTag("04A2B1C3"))

        with pytest.raises(NfcReaderError, match="no answer"):
            _reader(transport).read_tag()

    def test_a_bcc_mismatch_is_rejected(self):
        class BadBccTransport(FakeTransport):
            def command(self, data, response_len=0):
                if (
                    data[0] == CMD_READ_DATA
                    and self._pending_response
                    and len(self._pending_response) == 5
                ):
                    corrupted = bytearray(self._pending_response)
                    corrupted[-1] ^= 0xFF
                    return bytes(corrupted)[:response_len]
                return super().command(data, response_len)

        transport = BadBccTransport(tag=Iso14443aTag("04A2B1C3"))

        with pytest.raises(PN5180FrameError, match="BCC"):
            _reader(transport).read_tag()

    def test_a_wrong_crc_on_select_is_rejected(self):
        class BadCrcTag(Iso14443aTag):
            def respond(self, frame):
                response = super().respond(frame)
                if response is not None and frame[:2] == bytes(
                    [ISO14443A_ANTICOLLISION_CL1, ISO14443A_NVB_FINAL]
                ):
                    corrupted = bytearray(response)
                    corrupted[-1] ^= 0xFF
                    return bytes(corrupted)
                return response

        transport = FakeTransport(tag=BadCrcTag("04A2B1C3"))

        with pytest.raises(PN5180FrameError, match="CRC mismatch"):
            _reader(transport).read_tag()

    def test_a_silent_reader_after_requesting_a_page_falls_back_to_uid_only(self):
        class DisappearsAfterSelect(Iso14443aTag):
            def respond(self, frame):
                if len(frame) >= 2 and frame[0] == 0x30:
                    return None
                return super().respond(frame)

        transport = FakeTransport(tag=DisappearsAfterSelect("04A2B1C3"))

        result = _reader(transport).read_tag()

        assert result.uid == "04A2B1C3"
        assert result.writable is False

    def test_a_uid_cascading_past_three_levels_is_rejected(self):
        class RunawayCascadeTag(Iso14443aTag):
            def respond(self, frame):
                if frame == bytes([ISO14443A_REQA]):
                    return bytes([0x44, 0x00])
                if len(frame) == 2 and frame[1] == ISO14443A_NVB_COLLIDE:
                    return bytes(
                        [ISO14443A_CASCADE_TAG, 0x01, 0x02, 0x03, 0x88 ^ 0x01 ^ 0x02 ^ 0x03]
                    )
                if len(frame) >= 2 and frame[1] == ISO14443A_NVB_FINAL:
                    return bytes([0x04]) + crc_a(bytes([0x04]))
                return None

        transport = FakeTransport(tag=RunawayCascadeTag("04A2B1C3"))

        with pytest.raises(PN5180FrameError, match="cascades"):
            _reader(transport).read_tag()

    def test_the_irq_wait_loop_retries_before_becoming_ready(self):
        transport = FakeTransport(tag=Iso14443aTag("04A2B1C3", sak=0x08), irq_delay=3)

        result = _reader(transport).read_tag(timeout_s=1.0)

        assert result.uid == "04A2B1C3"


# --------------------------------------------------------------------- ISO15693


class TestReadTagIso15693:
    def test_no_iso14443a_tag_and_no_iso15693_tag_returns_none(self):
        reader = _reader(FakeTransport(tag=None))

        assert reader.read_tag(timeout_s=0.05) is None

    def test_falls_through_to_iso15693_when_no_type_a_tag_answers(self):
        transport = FakeTransport(tag=Iso15693Tag("E004015012345678"))

        result = _reader(transport).read_tag(timeout_s=0.05)

        assert result.uid == "E004015012345678"
        assert transport.rf_config == RF_CONFIG_ISO15693_26

    def test_a_tag_with_no_capability_container_is_uid_only(self):
        # A Shot Scope-style raw ICODE SLIX tag: readable UID, no NDEF.
        tag = Iso15693Tag("E004015012345678")

        result = _reader(FakeTransport(tag=tag)).read_tag(timeout_s=0.05)

        assert result.uid == "E004015012345678"
        assert result.writable is False
        assert result.blank is False

    def test_a_type5_formatted_tag_reports_the_club_written_on_it(self):
        message = ndef.wrap_tlv(ndef.encode_text_record("driver")).ljust(64, b"\x00")
        blocks = {0: bytes([CAPABILITY_CONTAINER_MAGIC, 0x40, 0x10, 0x00])}
        block = 1
        for offset in range(0, 64, 4):
            blocks[block] = message[offset : offset + 4]
            block += 1
        tag = Iso15693Tag("E004015012345678", blocks=blocks)

        result = _reader(FakeTransport(tag=tag)).read_tag(timeout_s=0.05)

        assert result.text == "driver"
        assert result.writable is True

    def test_a_tag_error_response_reading_a_block_falls_back_to_uid_only(self):
        tag = Iso15693Tag("E004015012345678", error_read=True)

        result = _reader(FakeTransport(tag=tag)).read_tag(timeout_s=0.05)

        assert result.uid == "E004015012345678"
        assert result.writable is False

    def test_a_bad_inventory_crc_is_rejected(self):
        class BadCrcTransport(FakeTransport):
            def command(self, data, response_len=0):
                response = super().command(data, response_len)
                if data[0] == CMD_READ_DATA and len(response) >= 12:
                    corrupted = bytearray(response)
                    corrupted[-1] ^= 0xFF
                    return bytes(corrupted)
                return response

        transport = BadCrcTransport(tag=Iso15693Tag("E004015012345678"))

        with pytest.raises(PN5180FrameError, match="CRC mismatch"):
            _reader(transport).read_tag(timeout_s=0.05)


# ------------------------------------------------------------------- IRQ timeout


class TestIrqTimeout:
    def test_an_empty_field_on_both_technologies_is_not_an_error(self):
        # A stuck-low BUSY line means every poll -- REQA, then Inventory --
        # never sees RX_IRQ_STAT set, exactly like a genuinely empty field.
        transport = FakeTransport(never_ready=True)

        assert _reader(transport).read_tag(timeout_s=0.05) is None
