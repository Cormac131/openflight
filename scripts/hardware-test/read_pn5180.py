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
from openflight.nfc.pn5180 import DEFAULT_SPI_BUS, DEFAULT_SPI_DEVICE


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
