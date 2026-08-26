#!/usr/bin/env python3
"""Bench-test the X9C104 digital pot fitted to the SEN-14262 R17 pads.

Sweeps or sets the wiper so you can verify the wiring with a multimeter across
the R17 pads before trusting the Debug page slider.

Wiring (BCM numbering; see docs/sound-trigger-wiring.md):
    X9C104 pin 7 CS  -> Pi BCM22 (physical 15)
    X9C104 pin 1 INC -> Pi BCM23 (physical 16)
    X9C104 pin 2 U/D -> Pi BCM24 (physical 18)
    X9C104 pin 8 VCC -> Pi 5V    (physical 2)
    X9C104 pin 4 VSS -> Pi GND   (physical 20)
    X9C104 pin 5 RW + pin 3 RH -> one R17 pad
    X9C104 pin 6 RL            -> the other R17 pad

Usage:
    # Step through the full range, pausing so you can read a meter
    uv run python scripts/hardware-test/test_x9c104.py --sweep

    # Park the wiper at one tap
    uv run python scripts/hardware-test/test_x9c104.py --position 46

Stop the OpenFlight server first: it holds the same GPIO lines.
"""

import argparse
import time

from openflight.sensitivity import (
    DEFAULT_POSITION,
    MAX_POSITION,
    X9C104,
    preamp_feedback_ohms,
    resistance_ohms,
)


def describe(position: int) -> str:
    """One line of what a tap means at the R17 pads and in the preamp."""
    return (
        f"position {position:2d}/{MAX_POSITION}  "
        f"R17 ~{resistance_ohms(position) / 1000:6.1f} kohm  "
        f"preamp ~{preamp_feedback_ohms(position) / 1000:5.1f} kohm"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cs-pin", type=int, default=22, help="BCM GPIO for CS (default: 22)")
    parser.add_argument("--inc-pin", type=int, default=23, help="BCM GPIO for INC (default: 23)")
    parser.add_argument("--ud-pin", type=int, default=24, help="BCM GPIO for U/D (default: 24)")
    parser.add_argument(
        "--position",
        type=int,
        default=None,
        help=f"Park the wiper at this tap (0..{MAX_POSITION})",
    )
    parser.add_argument("--sweep", action="store_true", help="Step through the whole range")
    parser.add_argument(
        "--sweep-step", type=int, default=10, help="Taps between sweep stops (default: 10)"
    )
    parser.add_argument(
        "--sweep-dwell", type=float, default=2.0, help="Seconds to hold each sweep stop"
    )
    args = parser.parse_args()

    if args.position is not None and not 0 <= args.position <= MAX_POSITION:
        parser.error(f"--position must be within 0..{MAX_POSITION}")
    if args.sweep_step < 1:
        parser.error("--sweep-step must be at least 1")

    pot = X9C104(cs_pin=args.cs_pin, inc_pin=args.inc_pin, ud_pin=args.ud_pin)
    try:
        print(
            f"Claiming CS=BCM{args.cs_pin}, INC=BCM{args.inc_pin}, U/D=BCM{args.ud_pin}",
        )
        pot.open()

        # The chip has no readback, so the only way to know where the wiper is
        # is to drive it against the RL end first.
        print("Calibrating: driving the wiper to the RL end (position 0)...")
        pot.calibrate()
        print(f"  {describe(0)}")

        if args.sweep:
            print("\nSweeping. Put a meter across the R17 pads.\n")
            for position in range(0, MAX_POSITION + 1, args.sweep_step):
                pot.set_position(position)
                print(f"  {describe(position)}")
                time.sleep(args.sweep_dwell)
            pot.set_position(MAX_POSITION)
            print(f"  {describe(MAX_POSITION)}")
            time.sleep(args.sweep_dwell)

        target = args.position if args.position is not None else DEFAULT_POSITION
        pot.set_position(target)
        print(f"\nParked at {describe(target)}")
        print(
            "\nNothing was written to the chip's non-volatile memory, so a power "
            "cycle restores whatever it held before."
        )
    finally:
        pot.close()


if __name__ == "__main__":
    main()
