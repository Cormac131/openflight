"""NXP PN532 I2C driver, scoped to reading passive-tag UIDs.

The PN532 is the reader in the club-tag setup: the tags themselves are cheap
passive NTAG/MIFARE stickers on the club grips, which carry no power and cannot
initiate anything. Only the UID is used -- the club mapping lives on the Pi in
``ClubTagRegistry``, so a tag never needs to be written and a blank sticker
straight off the roll works.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Protocol

from . import ndef
from .models import normalize_uid
from .reader import NfcReaderError, TagRead, TagWriteError

logger = logging.getLogger(__name__)

DEFAULT_I2C_ADDRESS = 0x24
DEFAULT_I2C_BUS = 1

PREAMBLE = 0x00
START_CODE_1 = 0x00
START_CODE_2 = 0xFF
POSTAMBLE = 0x00
HOST_TO_PN532 = 0xD4
PN532_TO_HOST = 0xD5
ACK_FRAME = bytes([0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00])
I2C_READY = 0x01

COMMAND_GET_FIRMWARE_VERSION = 0x02
COMMAND_SAM_CONFIGURATION = 0x14
COMMAND_RF_CONFIGURATION = 0x32
COMMAND_IN_LIST_PASSIVE_TARGET = 0x4A
COMMAND_IN_DATA_EXCHANGE = 0x40

# NFC Forum Type 2 (MIFARE Ultralight / NTAG) commands and layout.
TYPE2_READ = 0x30
TYPE2_WRITE = 0xA2
TYPE2_SAK = 0x00
TYPE2_PAGE_BYTES = 4
TYPE2_READ_BYTES = 16
CAPABILITY_CONTAINER_PAGE = 3
CAPABILITY_CONTAINER_MAGIC = 0xE1
FIRST_DATA_PAGE = 4
# A club record is around twenty bytes. Reading the first four pages-worth of
# user memory finds its TLV without waiting on a full 144-byte NTAG213 dump.
NDEF_SCAN_BYTES = 64

BAUD_TYPE_A_106KBPS = 0x00
# Longest InListPassiveTarget answer for one ISO14443A target with a 10-byte
# UID, plus frame overhead and the I2C status byte.
_MAX_RESPONSE_BYTES = 64


class PN532FrameError(NfcReaderError):
    """Raised when a frame from the PN532 is malformed."""


class I2CTransport(Protocol):  # pylint: disable=unnecessary-ellipsis
    """Byte-level I2C access, kept separate so frame logic is testable."""

    def write(self, data: bytes) -> None:
        """Write raw bytes to the device."""
        ...  # pylint: disable=unnecessary-ellipsis

    def read(self, length: int) -> bytes:
        """Read raw bytes from the device."""
        ...  # pylint: disable=unnecessary-ellipsis

    def close(self) -> None:
        """Close the underlying bus."""
        ...  # pylint: disable=unnecessary-ellipsis


class SMBusTransport:
    """I2CTransport backed by smbus2 raw transactions.

    ``read_i2c_block_data`` cannot be used: it writes a register byte first, and
    the PN532 has no register map -- it expects a bare read.
    """

    def __init__(self, *, bus_number: int = DEFAULT_I2C_BUS, address: int = DEFAULT_I2C_ADDRESS):
        from smbus2 import SMBus  # pylint: disable=import-outside-toplevel,import-error

        self.address = address
        self.bus_number = bus_number
        self._bus = SMBus(bus_number)
        self._closed = False

    def write(self, data: bytes) -> None:
        """Send one raw I2C write transaction."""
        from smbus2 import i2c_msg  # pylint: disable=import-outside-toplevel,import-error

        self._bus.i2c_rdwr(i2c_msg.write(self.address, data))

    def read(self, length: int) -> bytes:
        """Perform one raw I2C read transaction."""
        from smbus2 import i2c_msg  # pylint: disable=import-outside-toplevel,import-error

        message = i2c_msg.read(self.address, length)
        self._bus.i2c_rdwr(message)
        return bytes(bytearray(message))

    def close(self) -> None:
        """Close the bus once."""
        if self._closed:
            return
        self._closed = True
        self._bus.close()


def build_frame(data: bytes) -> bytes:
    """Wrap a command payload (TFI included) in a PN532 normal information frame."""
    if not 1 <= len(data) <= 254:
        raise PN532FrameError(f"Frame payload must be 1-254 bytes, got {len(data)}")
    length = len(data)
    length_checksum = (~length + 1) & 0xFF
    data_checksum = (~sum(data) + 1) & 0xFF
    return bytes(
        [
            PREAMBLE,
            START_CODE_1,
            START_CODE_2,
            length,
            length_checksum,
            *data,
            data_checksum,
            POSTAMBLE,
        ]
    )


def parse_frame(buffer: bytes) -> bytes:
    """Extract and verify one information frame, returning its payload.

    The buffer is whatever the bus handed back, which routinely carries leading
    padding and trailing junk because the read length is fixed while the frame
    is not.
    """
    start = buffer.find(bytes([START_CODE_1, START_CODE_2]))
    if start < 0:
        raise PN532FrameError(f"No start code in response: {buffer.hex()}")
    body = buffer[start + 2 :]
    if len(body) < 3:
        raise PN532FrameError(f"Truncated frame header: {buffer.hex()}")

    length, length_checksum = body[0], body[1]
    if (length + length_checksum) & 0xFF:
        raise PN532FrameError(f"Bad length checksum in {buffer.hex()}")
    if length == 0:
        raise PN532FrameError(f"Empty frame payload in {buffer.hex()}")

    payload = body[2 : 2 + length]
    if len(payload) < length:
        raise PN532FrameError(f"Frame shorter than its declared length: {buffer.hex()}")
    data_checksum = body[2 + length]
    if (sum(payload) + data_checksum) & 0xFF:
        raise PN532FrameError(f"Bad data checksum in {buffer.hex()}")
    return payload


def parse_passive_target(payload: bytes) -> Optional[tuple[str, int]]:
    """Return (UID, SAK) from an InListPassiveTarget response payload.

    ``payload`` starts at the target count. A count of zero is the normal
    "nothing on the antenna" answer, not an error. The SAK (SEL_RES) is what
    separates a writable Type 2 tag from a MIFARE Classic card.
    """
    if not payload:
        raise PN532FrameError("InListPassiveTarget response is empty")
    if payload[0] == 0:
        return None
    # Layout: NbTg, Tg, SENS_RES(2), SEL_RES, NFCIDLength, NFCID...
    if len(payload) < 6:
        raise PN532FrameError(f"Truncated target descriptor: {payload.hex()}")
    sak = payload[4]
    uid_length = payload[5]
    uid = payload[6 : 6 + uid_length]
    if len(uid) < uid_length:
        raise PN532FrameError(f"Truncated UID in {payload.hex()}")
    return normalize_uid(uid.hex()), sak


def parse_passive_target_uid(payload: bytes) -> Optional[str]:
    """Return just the UID from an InListPassiveTarget response payload."""
    target = parse_passive_target(payload)
    return target[0] if target else None


class PN532I2C:
    """Read passive-tag UIDs from a PN532 over I2C."""

    name = "pn532"

    def __init__(
        self,
        *,
        bus_number: int = DEFAULT_I2C_BUS,
        address: int = DEFAULT_I2C_ADDRESS,
        transport: I2CTransport | None = None,
        ack_timeout_s: float = 0.5,
    ):
        self.bus_number = bus_number
        self.address = address
        self.ack_timeout_s = ack_timeout_s
        self._transport = transport
        self._opened = False
        self.firmware_version: Optional[str] = None

    # ------------------------------------------------------------- lifecycle

    def open(self) -> None:
        """Wake the reader, confirm its identity, and configure it for polling."""
        if self._transport is None:
            self._transport = SMBusTransport(bus_number=self.bus_number, address=self.address)
        self._opened = True
        try:
            self.firmware_version = self._read_firmware_version()
            # One passive-activation retry keeps read_uid non-blocking: without
            # it the PN532 retries forever and never answers until a tag lands
            # on the antenna, which would stall the poll thread on shutdown.
            self._call(bytes([COMMAND_RF_CONFIGURATION, 0x05, 0xFF, 0x01, 0x01]))
            self._call(bytes([COMMAND_SAM_CONFIGURATION, 0x01, 0x14, 0x01]))
        except Exception:
            self.close()
            raise
        logger.info(
            "[NFC] PN532 ready on I2C-%d at 0x%02x (firmware %s)",
            self.bus_number,
            self.address,
            self.firmware_version,
        )

    def close(self) -> None:
        """Release the bus."""
        self._opened = False
        if self._transport is not None:
            try:
                self._transport.close()
            finally:
                self._transport = None

    # ---------------------------------------------------------------- public

    def read_uid(self, timeout_s: float = 0.5) -> Optional[str]:
        """Poll once for an ISO14443A tag, returning its UID or None."""
        target = self._poll(timeout_s)
        return target[0] if target else None

    def read_tag(self, timeout_s: float = 0.5) -> Optional[TagRead]:
        """Poll once, and read the tag's NDEF contents if it is a Type 2 tag."""
        target = self._poll(timeout_s)
        if target is None:
            return None
        uid, sak = target
        if sak != TYPE2_SAK:
            # MIFARE Classic and friends: usable by UID, but this driver does
            # not speak their authenticated block protocol.
            return TagRead(uid=uid, writable=False)
        try:
            memory = self._read_user_memory()
        except NfcReaderError as error:
            # The tag answered the poll but not the read -- it was lifted away,
            # or it is a Type 2 lookalike. Fall back to UID-only.
            logger.debug("[NFC] Could not read tag %s contents: %s", uid, error)
            return TagRead(uid=uid, writable=False)
        content = ndef.read_tag_content(memory)
        return TagRead(uid=uid, text=content.text, blank=content.blank, writable=True)

    def write_text(self, uid: str, text: str, timeout_s: float = 3.0) -> None:
        """Write one NDEF text record to the tag with this UID.

        Polls until that exact tag is on the antenna, writes, then reads back to
        confirm. A write that cannot be verified is reported as a failure: a
        half-written tag that silently "succeeds" would send the wrong club.
        """
        expected = normalize_uid(uid)
        payload = ndef.wrap_tlv(ndef.encode_text_record(text))
        deadline = time.monotonic() + timeout_s
        while True:
            target = self._poll(min(0.5, max(timeout_s, 0.1)))
            if target is not None and target[0] == expected:
                if target[1] != TYPE2_SAK:
                    raise TagWriteError("This tag type cannot be written")
                break
            if time.monotonic() >= deadline:
                raise TagWriteError("Tag not on the reader")

        pages = [
            payload[index : index + TYPE2_PAGE_BYTES].ljust(TYPE2_PAGE_BYTES, b"\x00")
            for index in range(0, len(payload), TYPE2_PAGE_BYTES)
        ]
        for offset, page in enumerate(pages):
            try:
                self._exchange(bytes([TYPE2_WRITE, FIRST_DATA_PAGE + offset]) + page)
            except NfcReaderError as error:
                raise TagWriteError(f"Write failed at page {FIRST_DATA_PAGE + offset}") from error

        if ndef.read_tag_content(self._read_user_memory()).text != text:
            raise TagWriteError("Tag did not read back what was written")

    # --------------------------------------------------------------- private

    def _poll(self, timeout_s: float) -> Optional[tuple[str, int]]:
        """Run one InListPassiveTarget, leaving any tag found selected."""
        payload = self._call(
            bytes([COMMAND_IN_LIST_PASSIVE_TARGET, 0x01, BAUD_TYPE_A_106KBPS]),
            timeout_s=timeout_s,
        )
        return parse_passive_target(payload)

    def _read_user_memory(self) -> bytes:
        """Read the start of a selected Type 2 tag's user memory.

        The capability container is checked first: a tag without the NDEF magic
        byte is not NDEF-formatted, and reading TLVs out of its memory would be
        interpreting somebody else's bytes as ours.
        """
        capability = self._exchange(bytes([TYPE2_READ, CAPABILITY_CONTAINER_PAGE]))
        if not capability or capability[0] != CAPABILITY_CONTAINER_MAGIC:
            raise NfcReaderError("Tag is not NDEF-formatted")

        memory = bytearray()
        page = FIRST_DATA_PAGE
        while len(memory) < NDEF_SCAN_BYTES:
            memory.extend(self._exchange(bytes([TYPE2_READ, page]))[:TYPE2_READ_BYTES])
            page += TYPE2_READ_BYTES // TYPE2_PAGE_BYTES
        return bytes(memory)

    def _exchange(self, data: bytes, *, timeout_s: float = 0.5) -> bytes:
        """Send one command to the selected tag and return its answer."""
        payload = self._call(
            bytes([COMMAND_IN_DATA_EXCHANGE, 0x01]) + data,
            timeout_s=timeout_s,
        )
        if not payload:
            raise PN532FrameError("InDataExchange returned no status")
        if payload[0] != 0x00:
            raise NfcReaderError(f"Tag exchange failed with status 0x{payload[0]:02x}")
        return payload[1:]

    # --------------------------------------------------------------- private

    def _read_firmware_version(self) -> str:
        payload = self._call(bytes([COMMAND_GET_FIRMWARE_VERSION]))
        if len(payload) < 4:
            raise PN532FrameError(f"Short firmware response: {payload.hex()}")
        chip, major, minor = payload[0], payload[1], payload[2]
        if chip != 0x32:
            raise NfcReaderError(
                f"Device at 0x{self.address:02x} is not a PN532 (IC byte 0x{chip:02x})"
            )
        return f"{major}.{minor}"

    def _call(self, command: bytes, *, timeout_s: float = 0.5) -> bytes:
        """Send one command and return its response payload (command echo stripped)."""
        transport = self._require_transport()
        transport.write(build_frame(bytes([HOST_TO_PN532]) + command))
        self._await_ack()

        deadline = time.monotonic() + timeout_s
        while not self._ready():
            if time.monotonic() >= deadline:
                raise NfcReaderError(f"PN532 did not answer command 0x{command[0]:02x} in time")
            time.sleep(0.01)

        # Drop the I2C status byte before the frame parser sees the buffer.
        payload = parse_frame(transport.read(_MAX_RESPONSE_BYTES)[1:])
        expected_echo = (command[0] + 1) & 0xFF
        if len(payload) < 2 or payload[0] != PN532_TO_HOST or payload[1] != expected_echo:
            raise PN532FrameError(f"Unexpected answer to 0x{command[0]:02x}: {payload.hex()}")
        return payload[2:]

    def _await_ack(self) -> None:
        """Block until the PN532 acknowledges the command frame."""
        transport = self._require_transport()
        deadline = time.monotonic() + self.ack_timeout_s
        while True:
            if self._ready():
                frame = transport.read(len(ACK_FRAME) + 1)[1:]
                if frame != ACK_FRAME:
                    raise PN532FrameError(f"Expected ACK, got {frame.hex()}")
                return
            if time.monotonic() >= deadline:
                raise NfcReaderError("PN532 did not acknowledge the command")
            time.sleep(0.005)

    def _ready(self) -> bool:
        """True once the PN532 has a frame waiting."""
        return bool(self._require_transport().read(1)[0] & I2C_READY)

    def _require_transport(self) -> I2CTransport:
        if self._transport is None or not self._opened:
            raise NfcReaderError("PN532 is not open")
        return self._transport
