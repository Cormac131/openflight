#!/usr/bin/env python3
"""Print club-tag UIDs from a PN532, with the club each one is mapped to."""

import argparse
import time

from openflight.nfc import (
    DEFAULT_I2C_ADDRESS,
    DEFAULT_IRQ_GPIO,
    DEFAULT_SPI_BUS,
    DEFAULT_SPI_DEVICE,
    PN532I2C,
    ClubTagRegistry,
    format_uid,
)


def main() -> None:
    """Poll the reader and print each tag presented."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interface",
        choices=("spi", "i2c"),
        default="spi",
        help="Host link (default: spi)",
    )
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
        "--irq-gpio",
        type=int,
        default=DEFAULT_IRQ_GPIO,
        help=f"BCM GPIO for IRQ (default: {DEFAULT_IRQ_GPIO})",
    )
    parser.add_argument("--bus", type=int, default=1, help="I2C bus number if --interface i2c")
    parser.add_argument(
        "--address",
        type=lambda value: int(value, 0),
        default=DEFAULT_I2C_ADDRESS,
        help=f"PN532 I2C address if --interface i2c (default: 0x{DEFAULT_I2C_ADDRESS:02x})",
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
    reader = PN532I2C(
        interface=args.interface,
        spi_bus=args.spi_bus,
        spi_device=args.spi_device,
        irq_gpio=args.irq_gpio,
        bus_number=args.bus,
        address=args.address,
    )
    reader.open()
    if args.interface == "spi":
        print(
            f"PN532 detected on SPI-{args.spi_bus}.{args.spi_device} "
            f"IRQ GPIO{args.irq_gpio} (firmware {reader.firmware_version})"
        )
    else:
        print(
            f"PN532 detected on I2C-{args.bus} at 0x{args.address:02x} "
            f"(firmware {reader.firmware_version})"
        )
    print(f"Club tags: {len(registry)} learned in {registry.path}")
    if args.assign:
        print(f"Present the tag to learn as {args.assign}...")
    else:
        print("Present a tag to the antenna (Ctrl-C to stop)...")

    try:
        seen = 0
        last_uid = None
        while args.count == 0 or seen < args.count:
            uid = reader.read_uid(timeout_s=1.0)
            if uid is None:
                last_uid = None
                time.sleep(args.interval)
                continue
            if uid == last_uid:
                # A tag left on the antenna reads continuously; print it once.
                time.sleep(args.interval)
                continue
            last_uid = uid

            if args.assign:
                tag = registry.assign(uid, args.assign)
                print(f"{format_uid(tag.uid)}  learned as {tag.club_id}")
                return

            club = registry.club_for(uid)
            print(f"{format_uid(uid)}  {club or '(not learned)'}")
            seen += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()


if __name__ == "__main__":
    main()
