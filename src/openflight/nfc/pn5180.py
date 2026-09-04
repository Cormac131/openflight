"""NXP PN5180 driver, scoped to reading passive-tag UIDs.

The PN5180 is a newer, more capable alternative to the PN532: it speaks both
ISO14443A (Type 2 / MIFARE Classic, same tags the PN532 reads) and ISO15693
(NFC Forum Type 5, ICODE SLIX/SLIX2 -- including Shot Scope's watch tags,
which the PN532 cannot see at all). Its only host link is SPI plus two GPIOs
(BUSY and RESET); there is no I2C fallback the way the PN532 has one.

Every poll tries ISO14443A first, then ISO15693, splitting the caller's
timeout between the two -- the chip's RF front end can only listen for one
technology at a time, so there is no way to listen for both at once the way
the PN532's InListPassiveTarget effectively does for its one technology.

Command framing, register addresses, and RF configuration values below follow
the NXP PN5180 datasheet's host interface and RF configuration tables. This
driver has not been exercised against physical silicon; treat a real PN5180's
behavior as authoritative over any assumption made here, and open an issue
with the discrepancy so the comment can be corrected.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Protocol

from . import ndef
from .models import normalize_uid
from .reader import NfcReaderError, TagRead
from .type2 import (
    CAPABILITY_CONTAINER_MAGIC,
    CAPABILITY_CONTAINER_PAGE,
    FIRST_DATA_PAGE,
    NDEF_SCAN_BYTES,
    TYPE2_PAGE_BYTES,
    TYPE2_READ,
    TYPE2_READ_BYTES,
    TYPE2_SAK,
)

logger = logging.getLogger(__name__)

DEFAULT_SPI_BUS = 0
DEFAULT_SPI_DEVICE = 0
DEFAULT_BUSY_GPIO = 23
DEFAULT_RESET_GPIO = 24
# The datasheet allows up to ~7 MHz; start conservative like the PN532 driver
# does, since board-to-board wiring quality on a breadboard prototype varies.
SPI_CLOCK_HZ = 2_000_000
BUSY_TIMEOUT_S = 0.5
RESET_PULSE_S = 0.002  # NRESET low pulse; datasheet minimum is 10us.
RESET_SETTLE_S = 0.003  # Boot time after reset before the host interface answers.

# Host interface command codes (PN5180 datasheet, "Host Interface Commands").
CMD_WRITE_REGISTER = 0x00
CMD_READ_REGISTER = 0x04
CMD_READ_EEPROM = 0x07
CMD_SEND_DATA = 0x09
CMD_READ_DATA = 0x0A
CMD_LOAD_RF_CONFIG = 0x11
CMD_RF_ON = 0x16
CMD_RF_OFF = 0x17

# Register addresses (subset -- only what club-tag inventory needs).
REG_IRQ_STATUS = 0x02
REG_IRQ_CLEAR = 0x03
REG_CRC_RX_CONFIG = 0x12
REG_RX_STATUS = 0x13
REG_CRC_TX_CONFIG = 0x19

RX_IRQ_STAT = 0x01  # IRQ_STATUS bit 0: a full RF frame was received.
RX_NUM_BYTES_MASK = 0x1FF  # RX_STATUS bits 0-8: bytes in the last received frame.

EEPROM_PRODUCT_VERSION = 0x10
EEPROM_FIRMWARE_VERSION = 0x12

# RF configuration profile bytes (TX, RX) for LOAD_RF_CONFIG.
RF_CONFIG_ISO14443A_106 = (0x00, 0x80)
RF_CONFIG_ISO15693_26 = (0x0D, 0x8D)

# ISO14443-3 Type A: REQA, anticollision, and SELECT.
ISO14443A_REQA = 0x26
ISO14443A_REQA_VALID_BITS = 7
ISO14443A_ANTICOLLISION_CL1 = 0x93
ISO14443A_ANTICOLLISION_CL2 = 0x95
ISO14443A_ANTICOLLISION_CL3 = 0x97
ISO14443A_NVB_COLLIDE = 0x20
ISO14443A_NVB_FINAL = 0x70
ISO14443A_CASCADE_TAG = 0x88
_CASCADE_LEVELS = (
    ISO14443A_ANTICOLLISION_CL1,
    ISO14443A_ANTICOLLISION_CL2,
    ISO14443A_ANTICOLLISION_CL3,
)

# ISO15693: single-slot Inventory, and addressed Read Single Block for
# tags formatted as an NFC Forum Type 5 tag.
ISO15693_FLAGS_INVENTORY = 0x26
ISO15693_FLAGS_ADDRESSED = 0x22
ISO15693_CMD_INVENTORY = 0x01
ISO15693_CMD_READ_SINGLE_BLOCK = 0x20
ISO15693_UID_BYTES = 8
ISO15693_ERROR_FLAG = 0x01
# ICODE SLIX/SLIX2 and most Type 5 tags use 4-byte blocks, same as a Type 2
# page. A tag with a different block size would need this made configurable.
ISO15693_BLOCK_BYTES = 4
ISO15693_CC_BLOCK = 0


class PN5180FrameError(NfcReaderError):
    """Raised when a response from the PN5180 is malformed."""


class Pn5180Bus(Protocol):  # pylint: disable=unnecessary-ellipsis
    """What the driver needs from the SPI+BUSY+RESET link, kept separate so
    frame logic is testable without real silicon or real GPIOs."""

    def reset(self) -> None:
        """Pulse NRESET, per the datasheet's power-up sequence."""
        ...  # pylint: disable=unnecessary-ellipsis

    def command(self, data: bytes, response_len: int = 0) -> bytes:
        """Send one host command, then read back ``response_len`` bytes."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Release the bus."""
        ...  # pylint: disable=unnecessary-ellipsis


class Pn5180SpiTransport:
    """SPI+BUSY+RESET transport for the PN5180's host interface.

    Unlike the PN532, the PN5180 has no software status byte to poll: a
    dedicated BUSY line is high while the chip is processing a command and
    low once it is ready for the next SPI transaction. Each host command is
    two separate SPI transactions -- write, then (for commands with an
    answer) read -- with NSS released and BUSY back at idle between them,
    per the datasheet's timing diagram. SPI is standard MSB-first here; the
    PN532's bit-reversal quirk does not apply.
    """

    def __init__(
        self,
        *,
        bus_number: int = DEFAULT_SPI_BUS,
        device: int = DEFAULT_SPI_DEVICE,
        busy_gpio: int = DEFAULT_BUSY_GPIO,
        reset_gpio: int = DEFAULT_RESET_GPIO,
        spi=None,
        busy=None,
        reset=None,
    ):
        self.bus_number = bus_number
        self.device = device
        self.busy_gpio = busy_gpio
        self.reset_gpio = reset_gpio
        self._owns_spi = spi is None
        # An injected SPI (as tests do) means an injected bus generally --
        # without this, omitting just one of busy/reset while injecting spi
        # would still reach for real gpiozero hardware for that one line.
        self._owns_busy = busy is None and spi is None
        self._owns_reset = reset is None and spi is None
        if spi is None:
            import spidev  # pylint: disable=import-outside-toplevel,import-error

            self._spi = spidev.SpiDev()
            self._spi.open(bus_number, device)
            self._spi.max_speed_hz = SPI_CLOCK_HZ
            self._spi.mode = 0
        else:
            self._spi = spi
        if self._owns_busy or self._owns_reset:
            from gpiozero import (  # pylint: disable=import-outside-toplevel
                DigitalInputDevice,
                DigitalOutputDevice,
            )

            from ..gpio_factory import (  # pylint: disable=import-outside-toplevel
                ensure_lgpio_pin_factory,
            )

            ensure_lgpio_pin_factory()
            self._busy = DigitalInputDevice(busy_gpio) if self._owns_busy else busy
            # active_high=False: on() drives NRESET low (asserted).
            self._reset = (
                DigitalOutputDevice(reset_gpio, active_high=False, initial_value=False)
                if self._owns_reset
                else reset
            )
        else:
            self._busy = busy
            self._reset = reset
        self._closed = False

    def reset(self) -> None:
        """Pulse NRESET low, then wait for the chip to boot back up."""
        if self._reset is None:
            return
        self._reset.on()
        time.sleep(RESET_PULSE_S)
        self._reset.off()
        time.sleep(RESET_SETTLE_S)

    def command(self, data: bytes, response_len: int = 0) -> bytes:
        """Write one command frame, then read back its answer, if any."""
        self._wait_idle()
        self._spi.xfer2(list(data))
        if response_len == 0:
            return b""
        self._wait_idle()
        return bytes(self._spi.xfer2([0x00] * response_len))

    def close(self) -> None:
        """Release SPI, BUSY, and RESET once."""
        if self._closed:
            return
        self._closed = True
        if self._owns_busy and self._busy is not None:
            try:
                self._busy.close()
            except AttributeError:
                pass
        if self._owns_reset and self._reset is not None:
            try:
                self._reset.close()
            except AttributeError:
                pass
        if self._owns_spi:
            self._spi.close()

    def _wait_idle(self) -> None:
        if self._busy is None:
            return
        deadline = time.monotonic() + BUSY_TIMEOUT_S
        while bool(self._busy.is_active):
            if time.monotonic() >= deadline:
                raise NfcReaderError(
                    "PN5180 BUSY line stuck high. Check the BUSY wire and that "
                    "NRESET is not held low."
                )
            time.sleep(0.0005)


def crc_a(data: bytes) -> bytes:
    """ISO/IEC 14443-3 CRC_A over ``data``, LSB byte first, as transmitted."""
    return _crc16(data, init=0x6363, invert=False)


def crc_iso15693(data: bytes) -> bytes:
    """ISO/IEC 15693-3 CRC over ``data``, LSB byte first, as transmitted."""
    return _crc16(data, init=0xFFFF, invert=True)


def _crc16(data: bytes, *, init: int, invert: bool) -> bytes:
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    if invert:
        crc ^= 0xFFFF
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


class Pn5180Spi:
    """Read passive-tag UIDs (and Type 2 / Type 5 NDEF content) from a PN5180."""

    name = "pn5180"

    def __init__(
        self,
        *,
        spi_bus: int = DEFAULT_SPI_BUS,
        spi_device: int = DEFAULT_SPI_DEVICE,
        busy_gpio: int = DEFAULT_BUSY_GPIO,
        reset_gpio: int = DEFAULT_RESET_GPIO,
        transport: Pn5180Bus | None = None,
    ):
        self.spi_bus = spi_bus
        self.spi_device = spi_device
        self.busy_gpio = busy_gpio
        self.reset_gpio = reset_gpio
        self._transport = transport
        self._opened = False
        self._rf_config: Optional[tuple[int, int]] = None
        self.firmware_version: Optional[str] = None
        self.product_version: Optional[str] = None

    # ------------------------------------------------------------- lifecycle

    def open(self) -> None:
        """Reset the chip, confirm its identity, and idle its RF field off."""
        if self._transport is None:
            self._transport = Pn5180SpiTransport(
                bus_number=self.spi_bus,
                device=self.spi_device,
                busy_gpio=self.busy_gpio,
                reset_gpio=self.reset_gpio,
            )
        self._opened = True
        try:
            self._transport.reset()
            self.firmware_version = self._read_version(EEPROM_FIRMWARE_VERSION)
            self.product_version = self._read_version(EEPROM_PRODUCT_VERSION)
            self._transport.command(bytes([CMD_RF_OFF, 0x00]))
            self._rf_config = None
        except Exception:
            self.close()
            raise
        logger.info(
            "[NFC] PN5180 ready on SPI-%d.%d BUSY GPIO%d RESET GPIO%d (firmware %s, product %s)",
            self.spi_bus,
            self.spi_device,
            self.busy_gpio,
            self.reset_gpio,
            self.firmware_version,
            self.product_version,
        )

    def close(self) -> None:
        """Turn the RF field off and release the bus."""
        self._opened = False
        if self._transport is not None:
            try:
                self._transport.command(bytes([CMD_RF_OFF, 0x00]))
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            try:
                self._transport.close()
            finally:
                self._transport = None

    # ---------------------------------------------------------------- public

    def read_tag(self, timeout_s: float = 0.5) -> Optional[TagRead]:
        """Poll ISO14443A, then ISO15693, splitting the timeout between them."""
        half = max(timeout_s / 2, 0.01)
        target = self._poll_iso14443a(half)
        if target is not None:
            return self._read_iso14443a(target)
        target = self._poll_iso15693(half)
        if target is not None:
            return self._read_iso15693(target)
        return None

    # ----------------------------------------------------------- ISO14443A

    def _read_iso14443a(self, target: tuple[str, int]) -> TagRead:
        uid, sak = target
        if sak != TYPE2_SAK:
            return TagRead(uid=uid, writable=False)
        try:
            memory = self._read_type2_memory()
        except NfcReaderError as error:
            logger.debug("[NFC] Could not read Type 2 tag %s contents: %s", uid, error)
            return TagRead(uid=uid, writable=False)
        content = ndef.read_tag_content(memory)
        return TagRead(uid=uid, text=content.text, blank=content.blank, writable=True)

    def _poll_iso14443a(self, timeout_s: float) -> Optional[tuple[str, int]]:
        """Run REQA + anticollision, leaving any tag found selected."""
        self._select_rf_config(RF_CONFIG_ISO14443A_106)
        atqa = self._iso14443a_transceive(
            bytes([ISO14443A_REQA]),
            valid_bits=ISO14443A_REQA_VALID_BITS,
            timeout_s=timeout_s,
            allow_timeout=True,
        )
        if atqa is None:
            return None

        uid_bytes = bytearray()
        for sel in _CASCADE_LEVELS:
            uid_block = self._iso14443a_anticollide(sel)
            sak = self._iso14443a_select(sel, uid_block)
            if uid_block[0] == ISO14443A_CASCADE_TAG:
                uid_bytes.extend(uid_block[1:])
                continue
            uid_bytes.extend(uid_block)
            return normalize_uid(bytes(uid_bytes).hex()), sak
        raise PN5180FrameError("UID cascades past the three ISO14443-3 levels")

    def _iso14443a_anticollide(self, sel: int) -> bytes:
        response = self._iso14443a_transceive(bytes([sel, ISO14443A_NVB_COLLIDE]))
        if response is None or len(response) != 5:
            raise PN5180FrameError(f"Bad anticollision response: {(response or b'').hex()}")
        uid_block, bcc = response[:4], response[4]
        if (uid_block[0] ^ uid_block[1] ^ uid_block[2] ^ uid_block[3]) != bcc:
            raise PN5180FrameError("Anticollision BCC mismatch")
        return uid_block

    def _iso14443a_select(self, sel: int, uid_block: bytes) -> int:
        bcc = uid_block[0] ^ uid_block[1] ^ uid_block[2] ^ uid_block[3]
        frame = bytes([sel, ISO14443A_NVB_FINAL, *uid_block, bcc])
        response = self._iso14443a_transceive(frame, append_crc=True, expect_crc=True)
        if response is None or len(response) != 1:
            raise PN5180FrameError(f"Bad SELECT response: {(response or b'').hex()}")
        return response[0]

    def _read_type2_memory(self) -> bytes:
        """Read the start of a selected Type 2 tag's user memory.

        The capability container is checked first: a tag without the NDEF
        magic byte is not NDEF-formatted, and reading TLVs out of its memory
        would be interpreting somebody else's bytes as ours.
        """
        capability = self._type2_read(CAPABILITY_CONTAINER_PAGE)
        if not capability or capability[0] != CAPABILITY_CONTAINER_MAGIC:
            raise NfcReaderError("Tag is not NDEF-formatted")

        memory = bytearray()
        page = FIRST_DATA_PAGE
        while len(memory) < NDEF_SCAN_BYTES:
            memory.extend(self._type2_read(page)[:TYPE2_READ_BYTES])
            page += TYPE2_READ_BYTES // TYPE2_PAGE_BYTES
        return bytes(memory)

    def _type2_read(self, page: int) -> bytes:
        response = self._iso14443a_transceive(
            bytes([TYPE2_READ, page]), append_crc=True, expect_crc=True
        )
        if response is None or len(response) != TYPE2_READ_BYTES:
            raise NfcReaderError(f"Bad Type 2 READ response for page {page}")
        return response

    def _iso14443a_transceive(  # pylint: disable=too-many-arguments
        self,
        data: bytes,
        *,
        valid_bits: int = 8,
        append_crc: bool = False,
        expect_crc: bool = False,
        timeout_s: float = 0.05,
        allow_timeout: bool = False,
    ) -> Optional[bytes]:
        """Send one ISO14443-3 frame and return the tag's answer, CRC stripped.

        ``allow_timeout`` turns "nothing answered" into ``None`` instead of an
        error -- only correct for REQA, where an empty field is the normal
        case. Every other exchange in a poll means a tag that just answered
        the previous step went silent, which is a real failure.
        """
        frame = data + (crc_a(data) if append_crc else b"")
        response = self._transceive(frame, valid_bits, timeout_s, allow_timeout)
        if response is None:
            return None
        if expect_crc:
            if len(response) < 2:
                raise PN5180FrameError(f"Response too short for CRC: {response.hex()}")
            payload, crc = response[:-2], response[-2:]
            if crc_a(payload) != crc:
                raise PN5180FrameError(f"CRC mismatch in response: {response.hex()}")
            return payload
        return response

    # ------------------------------------------------------------ ISO15693

    def _read_iso15693(self, uid: str) -> TagRead:
        try:
            memory = self._read_type5_memory(uid)
        except NfcReaderError as error:
            logger.debug("[NFC] Could not read ISO15693 tag %s contents: %s", uid, error)
            return TagRead(uid=uid, writable=False)
        content = ndef.read_tag_content(memory)
        return TagRead(uid=uid, text=content.text, blank=content.blank, writable=True)

    def _poll_iso15693(self, timeout_s: float) -> Optional[str]:
        """Run one single-slot Inventory command. Returns the UID, if any."""
        self._select_rf_config(RF_CONFIG_ISO15693_26)
        frame = bytes([ISO15693_FLAGS_INVENTORY, ISO15693_CMD_INVENTORY, 0x00])
        response = self._transceive(frame + crc_iso15693(frame), 8, timeout_s, allow_timeout=True)
        if response is None or len(response) < 1 + 1 + ISO15693_UID_BYTES + 2:
            return None
        if response[0] & ISO15693_ERROR_FLAG:
            return None
        payload, crc = response[:-2], response[-2:]
        if crc_iso15693(payload) != crc:
            raise PN5180FrameError(f"CRC mismatch in inventory response: {response.hex()}")
        # The UID is transmitted LSB first; display and storage order is MSB
        # first (manufacturer byte, e.g. 0xE0 for NXP, comes first).
        uid_wire = payload[2 : 2 + ISO15693_UID_BYTES]
        return normalize_uid(bytes(reversed(uid_wire)).hex())

    def _read_type5_memory(self, uid: str) -> bytes:
        capability = self._type5_read_block(uid, ISO15693_CC_BLOCK)
        if not capability or capability[0] != CAPABILITY_CONTAINER_MAGIC:
            raise NfcReaderError("Tag is not NDEF-formatted")

        memory = bytearray()
        block = ISO15693_CC_BLOCK + 1
        while len(memory) < NDEF_SCAN_BYTES:
            memory.extend(self._type5_read_block(uid, block))
            block += 1
        return bytes(memory)

    def _type5_read_block(self, uid: str, block: int) -> bytes:
        uid_wire = bytes(reversed(bytes.fromhex(uid)))
        frame = bytes([ISO15693_FLAGS_ADDRESSED, ISO15693_CMD_READ_SINGLE_BLOCK, *uid_wire, block])
        response = self._transceive(frame + crc_iso15693(frame), 8, 0.05, allow_timeout=False)
        # An error response is shorter (flags + one error-code byte) than a
        # successful one (flags + the block's data), so length alone cannot
        # tell "malformed" from "the tag declined" apart -- check the CRC and
        # the error flag before judging the payload's length.
        if response is None or len(response) < 1 + 2:
            raise NfcReaderError(f"Bad Read Single Block response for block {block}")
        payload, crc = response[:-2], response[-2:]
        if crc_iso15693(payload) != crc:
            raise PN5180FrameError(f"CRC mismatch reading block {block}: {response.hex()}")
        if payload[0] & ISO15693_ERROR_FLAG:
            raise NfcReaderError(f"Tag reported an error reading block {block}")
        if len(payload) != 1 + ISO15693_BLOCK_BYTES:
            raise NfcReaderError(f"Bad Read Single Block response for block {block}")
        return payload[1:]

    # --------------------------------------------------------------- private

    def _select_rf_config(self, profile: tuple[int, int]) -> None:
        """Load an RF profile, if it is not already active.

        Switching technology needs the field re-initialized: turn it off,
        load the new TX/RX configuration, disable the hardware's own CRC
        handling (the driver computes and checks CRCs itself, so behavior
        does not depend on register bits this driver is least sure of), then
        turn the field back on.
        """
        if self._rf_config == profile:
            return
        transport = self._require_transport()
        transport.command(bytes([CMD_RF_OFF, 0x00]))
        transport.command(bytes([CMD_LOAD_RF_CONFIG, *profile]))
        self._write_register(REG_CRC_TX_CONFIG, 0)
        self._write_register(REG_CRC_RX_CONFIG, 0)
        transport.command(bytes([CMD_RF_ON, 0x00]))
        self._rf_config = profile

    def _transceive(
        self,
        frame: bytes,
        valid_bits: int,
        timeout_s: float,
        allow_timeout: bool,
    ) -> Optional[bytes]:
        transport = self._require_transport()
        transport.command(bytes([CMD_SEND_DATA, valid_bits % 8]) + frame)
        if not self._wait_rx_irq(timeout_s):
            if allow_timeout:
                return None
            raise NfcReaderError("PN5180 got no answer from the tag")
        length = self._rx_byte_count()
        response = transport.command(bytes([CMD_READ_DATA, 0x00]), response_len=length)
        self._write_register(REG_IRQ_CLEAR, 0xFFFFFFFF)
        return response

    def _wait_rx_irq(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while True:
            if self._read_register(REG_IRQ_STATUS) & RX_IRQ_STAT:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.002)

    def _rx_byte_count(self) -> int:
        return self._read_register(REG_RX_STATUS) & RX_NUM_BYTES_MASK

    def _write_register(self, address: int, value: int) -> None:
        payload = (value & 0xFFFFFFFF).to_bytes(4, "little")
        self._require_transport().command(bytes([CMD_WRITE_REGISTER, address]) + payload)

    def _read_register(self, address: int) -> int:
        response = self._require_transport().command(
            bytes([CMD_READ_REGISTER, address]), response_len=4
        )
        return int.from_bytes(response, "little")

    def _read_version(self, eeprom_address: int) -> str:
        payload = self._require_transport().command(
            bytes([CMD_READ_EEPROM, eeprom_address, 0x02]), response_len=2
        )
        if len(payload) < 2:
            raise NfcReaderError(f"Short EEPROM response at 0x{eeprom_address:02x}")
        return f"{payload[1]}.{payload[0]}"

    def _require_transport(self) -> Pn5180Bus:
        if self._transport is None or not self._opened:
            raise NfcReaderError("PN5180 is not open")
        return self._transport
