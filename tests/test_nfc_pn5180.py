"""Tests for the PN5180 driver: the BUSY-gated transport, the register-level
sequencing the chip needs before it will transmit, and the ISO14443A /
ISO15693 poll logic built on top of them.
"""

from types import SimpleNamespace
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
    CMD_WRITE_REGISTER_AND_MASK,
    CMD_WRITE_REGISTER_OR_MASK,
    EEPROM_FIRMWARE_VERSION,
    EEPROM_PRODUCT_VERSION,
    ISO14443A_CASCADE_TAG,
    ISO14443A_NVB_COLLIDE,
    ISO14443A_NVB_FINAL,
    ISO14443A_REQA,
    ISO15693_CMD_INVENTORY,
    ISO15693_CMD_READ_SINGLE_BLOCK,
    ISO15693_ERROR_FLAG,
    REG_CRC_RX_CONFIG,
    REG_CRC_TX_CONFIG,
    REG_IRQ_CLEAR,
    REG_IRQ_STATUS,
    REG_RF_STATUS,
    REG_RX_STATUS,
    REG_SYSTEM_CONFIG,
    RF_CONFIG_ISO14443A_106,
    RF_CONFIG_ISO15693_26,
    RX_IRQ_STAT,
    SYSTEM_CONFIG_TRANSCEIVE,
    TRANSCEIVE_STATE_SHIFT,
    TRANSCEIVE_WAIT_TRANSMIT,
    TX_RFON_IRQ_STAT,
    PN5180FrameError,
    Pn5180Spi,
    Pn5180SpiTransport,
)
from openflight.nfc.reader import NfcReaderError
from openflight.nfc.type2 import CAPABILITY_CONTAINER_MAGIC, TYPE2_SAK

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
    """A BUSY line that pulses high for one poll after each SPI write."""

    def __init__(self, active=False, pulses=0):
        self.is_active = active
        self._pulses = pulses

    def pulse(self):
        if self._pulses > 0:
            self._pulses -= 1
            self.is_active = True

    def close(self):
        pass


class _PulsingBusy(_FakeBusy):
    """Goes high on the first read after a write, then low on the next."""

    def __init__(self):
        super().__init__(active=False)
        self.reads = 0

    @property
    def is_active(self):  # type: ignore[override]
        self.reads += 1
        return self.reads == 1

    @is_active.setter
    def is_active(self, _value):
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

    def test_a_response_read_waits_out_the_busy_pulse(self):
        # BUSY high means the chip has not staged its answer yet: reading
        # through it is what returned the previous command's bytes.
        busy = _PulsingBusy()
        spi = _FakeSpi()
        transport = Pn5180SpiTransport(spi=spi, busy=busy, reset=None)

        transport.command(bytes([0x04, 0x02]), response_len=4)

        assert len(spi.xfers) == 2
        assert busy.reads > 1  # saw it high, then waited for low

    def test_busy_stuck_high_times_out(self):
        transport = Pn5180SpiTransport(spi=_FakeSpi(), busy=_FakeBusy(active=True), reset=None)

        with pytest.raises(NfcReaderError, match="BUSY"):
            transport.command(bytes([0x16, 0x00]))

    def test_busy_reports_its_level_for_the_probe(self):
        transport = Pn5180SpiTransport(spi=_FakeSpi(), busy=_FakeBusy(active=True), reset=None)

        assert transport.busy is True

    def test_close_releases_busy_reset_and_spi_once(self):
        transport = Pn5180SpiTransport(spi=_FakeSpi(), busy=_FakeBusy(), reset=_FakeReset())

        transport.close()
        transport.close()  # must be idempotent


# ------------------------------------------------------------------ fake host bus


class Iso14443aTag:
    """Answers the request shapes the PN5180 driver sends for one A tag.

    Responses are CRC-free: the chip's own CRC engine strips a received CRC
    before the host ever sees the frame.
    """

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
                return bytes([self.sak if is_final else 0x04])
        if len(frame) >= 2 and frame[0] == 0x30:  # TYPE2_READ
            return self.type2_pages.get(frame[1], bytes(16))
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
            return bytes([0x00, 0x00]) + bytes(reversed(self.uid))
        if cmd == ISO15693_CMD_READ_SINGLE_BLOCK:
            if bytes(reversed(frame[2:10])) != self.uid:
                return None
            if self.error_read:
                return bytes([ISO15693_ERROR_FLAG, 0x0F])
            return bytes([0x00]) + self.blocks.get(frame[10], bytes(4))
        return None


class FakeTransport:
    """A PN5180 bus backed by a simulated tag and a simulated register file.

    Register masks, the transceive state machine, and the IRQ status bits are
    modelled rather than stubbed, so the sequencing the real chip demands
    before it will transmit is actually exercised.
    """

    def __init__(self, *, tag=None, irq_delay=0, never_ready=False):
        self.tag = tag
        self.registers: dict[int, int] = {}
        self.writes: list[bytes] = []
        self.sends: list[SimpleNamespace] = []
        self.reset_count = 0
        self.closed = False
        self.rf_config = None
        self._pending_response: Optional[bytes] = None
        self._irq = 0
        self._irq_delay = irq_delay
        self._never_ready = never_ready

    def reset(self) -> None:
        self.reset_count += 1

    def command(self, data: bytes, response_len: int = 0) -> bytes:
        self.writes.append(bytes(data))
        cmd = data[0]
        if cmd == CMD_WRITE_REGISTER:
            return self._write_register(data[1], int.from_bytes(data[2:6], "little"))
        if cmd == CMD_WRITE_REGISTER_OR_MASK:
            value = self.registers.get(data[1], 0) | int.from_bytes(data[2:6], "little")
            self.registers[data[1]] = value
            return b""
        if cmd == CMD_WRITE_REGISTER_AND_MASK:
            value = self.registers.get(data[1], 0) & int.from_bytes(data[2:6], "little")
            self.registers[data[1]] = value
            return b""
        if cmd == CMD_READ_REGISTER:
            return self._read_register(data[1])
        if cmd == CMD_READ_EEPROM:
            return self._read_eeprom(data[1], response_len)
        if cmd == CMD_LOAD_RF_CONFIG:
            self.rf_config = (data[1], data[2])
            # A profile load reinstalls the CRC registers with CRC enabled.
            self.registers[REG_CRC_TX_CONFIG] = 0x01
            self.registers[REG_CRC_RX_CONFIG] = 0x01
            return b""
        if cmd == CMD_RF_OFF:
            return b""
        if cmd == CMD_RF_ON:
            self._irq |= TX_RFON_IRQ_STAT
            return b""
        if cmd == CMD_SEND_DATA:
            return self._send_data(bytes(data[2:]))
        if cmd == CMD_READ_DATA:
            return (self._pending_response or b"")[:response_len]
        raise AssertionError(f"Unhandled PN5180 command 0x{cmd:02x}")

    def close(self) -> None:
        self.closed = True

    @property
    def armed(self) -> bool:
        """True when the transceive state machine has been walked to Transceive."""
        return self.registers.get(REG_SYSTEM_CONFIG, 0) & 0x07 == SYSTEM_CONFIG_TRANSCEIVE

    @property
    def crc_enabled(self) -> bool:
        return bool(self.registers.get(REG_CRC_TX_CONFIG, 0) & 0x01)

    def _write_register(self, address: int, value: int) -> bytes:
        if address == REG_IRQ_CLEAR:
            self._irq &= ~value & 0xFFFFFFFF
            return b""
        self.registers[address] = value
        return b""

    def _read_register(self, address: int) -> bytes:
        if address == REG_IRQ_STATUS:
            return self._irq_status().to_bytes(4, "little")
        if address == REG_RX_STATUS:
            return len(self._pending_response or b"").to_bytes(4, "little")
        if address == REG_RF_STATUS:
            return (TRANSCEIVE_WAIT_TRANSMIT << TRANSCEIVE_STATE_SHIFT).to_bytes(4, "little")
        return self.registers.get(address, 0).to_bytes(4, "little")

    def _read_eeprom(self, address: int, response_len: int) -> bytes:
        if address == EEPROM_FIRMWARE_VERSION:
            return bytes([0x06, 0x01])
        if address == EEPROM_PRODUCT_VERSION:
            return bytes([0x02, 0x01])
        return bytes(response_len)

    def _send_data(self, frame: bytes) -> bytes:
        self.sends.append(SimpleNamespace(frame=frame, armed=self.armed, crc=self.crc_enabled))
        # The real chip only transmits from the Transceive state.
        self._pending_response = (
            self.tag.respond(frame) if (self.tag is not None and self.armed) else None
        )
        if self._pending_response is not None:
            self._irq |= RX_IRQ_STAT
        return b""

    def _irq_status(self) -> int:
        if self._never_ready:
            return self._irq & ~RX_IRQ_STAT
        if self._irq_delay > 0 and self._irq & RX_IRQ_STAT:
            self._irq_delay -= 1
            return self._irq & ~RX_IRQ_STAT
        return self._irq


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

    def test_identify_reports_chip_state_for_the_probe(self):
        reader = _reader(FakeTransport())

        identity = reader.identify()

        assert identity["firmware_version"] == "1.6"
        assert identity["transceive_state"] == TRANSCEIVE_WAIT_TRANSMIT
        assert len(identity["die_id"]) == 32  # 16 bytes, hex


# ----------------------------------------------------- chip-level sequencing


class TestTransceiveSequencing:
    """Regression tests for the three things that stop a PN5180 transmitting."""

    def test_every_frame_is_sent_from_the_transceive_state(self):
        transport = FakeTransport(tag=Iso14443aTag("04A2B1C3", sak=0x08))

        _reader(transport).read_tag()

        assert transport.sends
        assert all(send.armed for send in transport.sends)

    def test_crc_is_off_for_anticollision_and_on_for_select(self):
        transport = FakeTransport(tag=Iso14443aTag("04A2B1C3", sak=0x08))

        _reader(transport).read_tag()

        by_kind = {}
        for send in transport.sends:
            if len(send.frame) == 2 and send.frame[1] == ISO14443A_NVB_COLLIDE:
                by_kind["anticollision"] = send.crc
            elif len(send.frame) > 2 and send.frame[1] == ISO14443A_NVB_FINAL:
                by_kind["select"] = send.crc
        assert by_kind == {"anticollision": False, "select": True}

    def test_type2_reads_run_with_crc_enabled(self):
        pages = {3: bytes([CAPABILITY_CONTAINER_MAGIC, 0x10, 0x12, 0x00]).ljust(16, b"\x00")}
        transport = FakeTransport(tag=Iso14443aTag("04A2B1C3", type2_pages=pages))

        _reader(transport).read_tag()

        reads = [send for send in transport.sends if send.frame[0] == 0x30]
        assert reads and all(send.crc for send in reads)

    def test_disabling_crc_uses_a_masked_write_not_a_whole_register_write(self):
        # A plain write would clobber the rest of the profile LOAD_RF_CONFIG
        # just installed in these registers.
        transport = FakeTransport(tag=Iso14443aTag("04A2B1C3", sak=0x08))

        _reader(transport).read_tag()

        crc_writes = [
            write
            for write in transport.writes
            if write[0] == CMD_WRITE_REGISTER and write[1] in (REG_CRC_TX_CONFIG, REG_CRC_RX_CONFIG)
        ]
        assert crc_writes == []

    def test_the_rf_field_is_switched_on_before_polling(self):
        transport = FakeTransport(tag=Iso14443aTag("04A2B1C3", sak=0x08))

        _reader(transport).read_tag()

        rf_on_at = next(i for i, write in enumerate(transport.writes) if write[0] == CMD_RF_ON)
        first_send_at = next(
            i for i, write in enumerate(transport.writes) if write[0] == CMD_SEND_DATA
        )
        assert rf_on_at < first_send_at

    def test_an_unarmed_transceiver_reads_nothing(self):
        # The failure this whole sequence exists to prevent: the chip answers
        # register reads happily and simply never transmits.
        class NeverArms(FakeTransport):
            @property
            def armed(self):
                return False

        transport = NeverArms(tag=Iso14443aTag("04A2B1C3", sak=0x08))

        assert _reader(transport).read_tag(timeout_s=0.05) is None


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

    def test_read_uid_returns_just_the_uid(self):
        tag = Iso14443aTag("04A2B1C3", sak=0x08)

        assert _reader(FakeTransport(tag=tag)).read_uid() == "04A2B1C3"

    def test_a_factory_fresh_type2_tag_reads_as_blank_and_writable(self):
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
        class BadBccTag(Iso14443aTag):
            def respond(self, frame):
                response = super().respond(frame)
                if response is not None and len(response) == 5:
                    return response[:4] + bytes([response[4] ^ 0xFF])
                return response

        transport = FakeTransport(tag=BadBccTag("04A2B1C3"))

        with pytest.raises(PN5180FrameError, match="BCC"):
            _reader(transport).read_tag()

    def test_a_silent_tag_mid_page_read_falls_back_to_uid_only(self):
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
                    block = bytes([ISO14443A_CASCADE_TAG, 0x01, 0x02, 0x03])
                    bcc = block[0] ^ block[1] ^ block[2] ^ block[3]
                    return block + bytes([bcc])
                if len(frame) >= 2 and frame[1] == ISO14443A_NVB_FINAL:
                    return bytes([0x04])
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
    def test_no_tag_of_either_technology_returns_none(self):
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

    def test_an_inventory_error_response_reads_as_no_tag(self):
        class ErroringTag(Iso15693Tag):
            def respond(self, frame):
                if len(frame) >= 2 and frame[1] == ISO15693_CMD_INVENTORY:
                    return bytes([ISO15693_ERROR_FLAG, 0x0F])
                return super().respond(frame)

        transport = FakeTransport(tag=ErroringTag("E004015012345678"))

        assert _reader(transport).read_tag(timeout_s=0.05) is None

    def test_the_uid_is_reversed_from_wire_order(self):
        # ISO15693 sends the UID LSB first; it is stored and shown MSB first,
        # starting with the manufacturer byte.
        transport = FakeTransport(tag=Iso15693Tag("E004015012345678"))

        result = _reader(transport).read_tag(timeout_s=0.05)

        inventory = next(
            send
            for send in transport.sends
            if len(send.frame) > 1 and send.frame[1] == ISO15693_CMD_INVENTORY
        )
        assert inventory.frame[0] == 0x26
        assert result.uid.startswith("E0")


# ------------------------------------------------------------------- IRQ timeout


class TestIrqTimeout:
    def test_an_empty_field_on_both_technologies_is_not_an_error(self):
        transport = FakeTransport(never_ready=True)

        assert _reader(transport).read_tag(timeout_s=0.05) is None
