#!/usr/bin/env python3
"""Print club-tag UIDs from a PN5180, with the club each one is mapped to."""

import argparse
import time

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
    REG_IRQ_STATUS,
    REG_RX_STATUS,
    RF_CONFIG_ISO14443A_106,
    RF_CONFIG_ISO15693_26,
    TRANSCEIVE_WAIT_TRANSMIT,
)


def _probe(reader: Pn5180Spi) -> None:
    """Walk the layers between "SPI answers" and "a tag was read".

    Each step below can fail independently, and each has a different fix, so
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
    expected = "WaitTransmit" if state == TRANSCEIVE_WAIT_TRANSMIT else "unexpected"
    print(f"  transceive state {state} ({expected})")

    for label, profile in (
        ("ISO14443A 106k", RF_CONFIG_ISO14443A_106),
        ("ISO15693 26k", RF_CONFIG_ISO15693_26),
    ):
        print(f"--- {label} ---")
        # pylint: disable=protected-access
        reader._select_rf_config(profile)
        reader._set_crc(False)
        reader._write_register(0x03, 0x000FFFFF)  # IRQ_CLEAR
        reader._start_transceive()
        print(f"  transceive state after arming: {reader._transceive_state()}")
        frame = bytes([ISO14443A_REQA])
        valid_bits = ISO14443A_REQA_VALID_BITS
        if profile == RF_CONFIG_ISO15693_26:
            reader._set_crc(True)
            frame = bytes([0x26, 0x01, 0x00])  # single-slot Inventory
            valid_bits = 8
        try:
            answer = reader._transceive(
                frame, valid_bits=valid_bits, timeout_s=0.2, allow_timeout=True
            )
        except Exception as error:  # pylint: disable=broad-except
            print(f"  transceive raised: {error}")
            continue
        irq = reader._read_register(REG_IRQ_STATUS)
        rx = reader._read_register(REG_RX_STATUS)
        print(f"  sent {frame.hex()} ({valid_bits} valid bits)")
        print(f"  IRQ_STATUS 0x{irq:08x}   RX_STATUS 0x{rx:08x}")
        print(f"  answer: {answer.hex() if answer else '(none -- no tag, or no transmit)'}")

    print()
    print("Reading: an answer above with a tag on the antenna means the RF layer works.")
    print("No answer on both, with a tag present, points at the RF field or antenna;")
    print("a transceive state that never reaches WaitTransmit points at the host link.")


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
    main()
