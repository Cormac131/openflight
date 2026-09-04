#!/usr/bin/env python3
"""Print club-tag UIDs from a PN5180, with the club each one is mapped to."""

import argparse
import time

from openflight.gpio_factory import close_pin_factory
from openflight.nfc import (
    DEFAULT_BUSY_GPIO,
    DEFAULT_RESET_GPIO,
    ClubTagRegistry,
    Pn5180Spi,
    format_uid,
)
from openflight.nfc.pn5180 import (
    DEFAULT_SPI_BUS,
    DEFAULT_SPI_DEVICE,
    ISO14443A_REQA,
    ISO14443A_REQA_VALID_BITS,
    ISO15693_CMD_INVENTORY,
    ISO15693_FLAGS_INVENTORY,
    REG_IRQ_STATUS,
    REG_RX_STATUS,
    RF_CONFIG_ISO14443A_106,
    RF_CONFIG_ISO15693_26,
    TRANSCEIVE_WAIT_TRANSMIT,
)

# IRQ_STATUS bits worth naming in a probe report. "TX set, RX clear" is the
# whole diagnosis in two words: the chip transmitted and nothing answered.
IRQ_BITS = (
    (0x0001, "RX"),
    (0x0002, "TX"),
    (0x0004, "IDLE"),
    (0x0200, "RFON"),
    (0x4000, "RX_SOF"),
)


def _describe_irq(value: int) -> str:
    names = [name for bit, name in IRQ_BITS if value & bit]
    return "+".join(names) if names else "none"


def _probe_technology(reader: Pn5180Spi, label: str, profile, seconds: float) -> None:
    """Send one technology's opening frame repeatedly for a few seconds."""
    print(f"--- {label} ---")
    # pylint: disable=protected-access
    reader._select_rf_config(profile)
    if profile == RF_CONFIG_ISO15693_26:
        reader._set_crc(True)
        frame = bytes([ISO15693_FLAGS_INVENTORY, ISO15693_CMD_INVENTORY, 0x00])
        valid_bits = 8
    else:
        reader._set_crc(False)
        frame = bytes([ISO14443A_REQA])
        valid_bits = ISO14443A_REQA_VALID_BITS
    print(f"  sending {frame.hex()} ({valid_bits} valid bits) for {seconds:.0f}s")

    deadline = time.monotonic() + seconds
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            answer = reader._transceive(
                frame, valid_bits=valid_bits, timeout_s=0.2, allow_timeout=True
            )
        except Exception as error:  # pylint: disable=broad-except
            print(f"  transceive raised: {error}")
            return
        if answer:
            irq = reader._read_register(REG_IRQ_STATUS)
            print(f"  ANSWER after {attempts} tries: {answer.hex()}")
            print(f"  IRQ_STATUS 0x{irq:08x} ({_describe_irq(irq)})")
            return
        time.sleep(0.05)

    irq = reader._read_register(REG_IRQ_STATUS)
    rx = reader._read_register(REG_RX_STATUS)
    print(f"  no answer in {attempts} tries")
    print(f"  IRQ_STATUS 0x{irq:08x} ({_describe_irq(irq)})   RX_STATUS 0x{rx:08x}")
    if irq & 0x0002 and not irq & 0x0001:
        print("  ^ TX set, RX clear: the chip transmitted and nothing replied.")


def _probe(reader: Pn5180Spi, seconds: float = 5.0) -> None:
    """Walk the layers between "SPI answers" and "a tag was read".

    Each step below can fail independently and each has a different fix, so
    the point is to name which one broke rather than reporting "no tag".
    """
    identity = reader.identify()
    print("--- chip identity (EEPROM) ---")
    print(f"  die id           {identity['die_id']}")
    print(f"  product version  {identity['product_version']}")
    print(f"  firmware version {identity['firmware_version']}")
    print(f"  eeprom version   {identity['eeprom_version']}")
    if set(identity["die_id"]) <= {"0", "f"}:
        print("  ^ all 0s or all Fs means MISO is not returning data: check pin 21.")

    print("--- registers ---")
    for name in ("system_config", "irq_status", "rf_status", "rx_status"):
        print(f"  {name:<16} 0x{identity[name]:08x}")
    state = identity["transceive_state"]
    expected = "WaitTransmit" if state == TRANSCEIVE_WAIT_TRANSMIT else "idle/unexpected"
    print(f"  transceive state {state} ({expected}, before arming)")

    print()
    print(">>> HOLD A TAG FLAT AGAINST THE ANTENNA NOW <<<")
    print("    Each technology below is polled for a few seconds.")
    print()
    _probe_technology(reader, "ISO14443A 106k (NTAG, MIFARE)", RF_CONFIG_ISO14443A_106, seconds)
    _probe_technology(
        reader, "ISO15693 26k (ICODE SLIX, Shot Scope)", RF_CONFIG_ISO15693_26, seconds
    )

    print()
    print("Reading the result:")
    print("  ANSWER on either line          -> the RF layer works end to end.")
    print("  TX set, RX clear, tag present  -> field or antenna: check 3.3V, and that")
    print("                                    the tag is flat on the coil, not edge-on.")
    print("  transceive state never 1       -> host link, not RF.")


def main() -> None:
    """Poll the reader and print each tag presented."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spi-bus",
        type=int,
        default=DEFAULT_SPI_BUS,
        help=f"SPI bus (default: {DEFAULT_SPI_BUS})",
    )
    parser.add_argument(
        "--spi-device",
        type=int,
        default=DEFAULT_SPI_DEVICE,
        help=f"SPI CE / chip-select (default: {DEFAULT_SPI_DEVICE})",
    )
    parser.add_argument(
        "--busy-gpio",
        type=int,
        default=DEFAULT_BUSY_GPIO,
        help=f"BCM GPIO for BUSY (default: {DEFAULT_BUSY_GPIO})",
    )
    parser.add_argument(
        "--reset-gpio",
        type=int,
        default=DEFAULT_RESET_GPIO,
        help=f"BCM GPIO for NRESET (default: {DEFAULT_RESET_GPIO})",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Dump chip identity, registers, and one raw poll per technology, then exit",
    )
    parser.add_argument("--interval", type=float, default=0.2, help="Seconds between polls")
    parser.add_argument("--count", type=int, default=0, help="Tags to print; 0 runs forever")
    parser.add_argument(
        "--tags-file",
        default=None,
        help="Learned club tags to resolve against (default: ~/.openflight/club_tags.json)",
    )
    parser.add_argument(
        "--assign",
        default=None,
        metavar="CLUB",
        help="Learn the next tag presented as CLUB (e.g. 7-iron) and exit",
    )
    args = parser.parse_args()

    registry = ClubTagRegistry(args.tags_file)
    reader = Pn5180Spi(
        spi_bus=args.spi_bus,
        spi_device=args.spi_device,
        busy_gpio=args.busy_gpio,
        reset_gpio=args.reset_gpio,
    )
    reader.open()
    print(
        f"PN5180 detected on SPI-{args.spi_bus}.{args.spi_device} "
        f"BUSY GPIO{args.busy_gpio} RESET GPIO{args.reset_gpio} "
        f"(firmware {reader.firmware_version}, product {reader.product_version})"
    )
    if args.probe:
        try:
            _probe(reader)
        finally:
            reader.close()
        return

    print(f"Club tags: {len(registry)} learned in {registry.path}")
    if args.assign:
        print(f"Present the tag to learn as {args.assign}...")
    else:
        print("Present a tag to the antenna -- ISO14443A or ISO15693 (Ctrl-C to stop)...")

    try:
        seen = 0
        last_uid = None
        while args.count == 0 or seen < args.count:
            tag = reader.read_tag(timeout_s=1.0)
            if tag is None:
                last_uid = None
                time.sleep(args.interval)
                continue
            if tag.uid == last_uid:
                # A tag left on the antenna reads continuously; print it once.
                time.sleep(args.interval)
                continue
            last_uid = tag.uid

            if args.assign:
                learned = registry.assign(tag.uid, args.assign)
                print(f"{format_uid(learned.uid)}  learned as {learned.club_id}")
                return

            club = registry.club_for(tag.uid)
            print(f"{format_uid(tag.uid)}  {club or '(not learned)'}")
            seen += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        # Without this a one-shot run exits through gpiozero's still-running
        # background thread, which prints an alarming "could not acquire lock
        # for <stderr> at interpreter shutdown" for what is a clean exit.
        close_pin_factory()
