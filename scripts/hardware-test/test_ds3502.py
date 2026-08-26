#!/usr/bin/env python3
"""Bench-test the DS3502 digital pot fitted to the SEN-14262 R17 pads.

Three modes:

``--sweep``
    Steps the wiper across its range so you can watch the resistance change on
    a multimeter. Proves the pot is wired and responding.

``--noise-floor``
    Steps the wiper and, at each step, counts `GATE` edges on the sound
    trigger's GPIO. Run it in the room you hit in, while it is quiet: the step
    where edges start appearing is where ambient noise begins firing the
    trigger, so the usable setting sits below it.

``--position N``
    Parks the wiper at one step. Unlike the three-wire pots this replaced, the
    DS3502 holds its position with nothing connected, so there is no --hold to
    worry about; add ``--store`` to commit it to the chip's EEPROM.

Wiring (see docs/sound-trigger-wiring.md):
    DS3502 VCC -> Pi 3.3V     DS3502 SDA -> Pi SDA (BCM2, physical 3)
    DS3502 GND -> Pi GND      DS3502 SCL -> Pi SCL (BCM3, physical 5)
    DS3502 V+  -> Pi 5V       (analog supply, needs 4.5V or more)
    DS3502 RL  -> one R17 pad
    DS3502 RW  -> series resistor -> the other R17 pad

Usage:
    uv run python scripts/hardware-test/test_ds3502.py --sweep
    uv run python scripts/hardware-test/test_ds3502.py --noise-floor
    uv run python scripts/hardware-test/test_ds3502.py --position 64 --store

Stop the OpenFlight server first if you are also using --noise-floor: it holds
the trigger GPIO.
"""

import argparse
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from openflight.sensitivity import (
    DEFAULT_ADDRESS,
    DEFAULT_SERIES_OHMS,
    DS3502,
    MAX_POSITION,
    preamp_feedback_ohms,
    resistance_ohms,
    validate_address,
)

QUIET = "quiet"
ACTIVE = "active"
SATURATED = "saturated"

# Above this share of the window spent high, GATE is effectively stuck on
# rather than pulsing. Counting edges alone would call that "quiet" -- there are
# no transitions -- which is exactly backwards.
SATURATED_HIGH_FRACTION = 0.9

DEFAULT_MARGIN_STEPS = 6


@dataclass
class StepObservation:
    """What the trigger line did while the wiper sat at one step."""

    position: int
    edges: int
    high_fraction: float

    @property
    def verdict(self) -> str:
        """Classification for this window."""
        return classify_window(self.edges, self.high_fraction)


def classify_window(edges: int, high_fraction: float) -> str:
    """Label one dwell window on the GATE line.

    Saturation is checked before edges on purpose: a latched-high GATE produces
    no transitions at all, so an edge-count-only test would report the worst
    case as the quietest.
    """
    if high_fraction >= SATURATED_HIGH_FRACTION:
        return SATURATED
    if edges > 0:
        return ACTIVE
    return QUIET


def recommend_position(
    observations: List[StepObservation], *, margin: int = DEFAULT_MARGIN_STEPS
) -> Tuple[Optional[int], str]:
    """Suggest a step from a quiet-room sweep, with the reasoning."""
    if not observations:
        return None, "No steps were sampled."

    ordered = sorted(observations, key=lambda item: item.position)
    lowest = ordered[0]
    if lowest.verdict != QUIET:
        return None, (
            f"Even the least sensitive step ({lowest.position}) saw the trigger "
            f"{lowest.verdict}. That is not a gain problem: check the GATE wiring, "
            "the R17 pad joints, and whether the room is genuinely quiet."
        )

    noisy = [item for item in ordered if item.verdict != QUIET]
    if not noisy:
        top = ordered[-1]
        return top.position, (
            f"Nothing fired the trigger anywhere up to step {top.position}, so this "
            "room never reached the noise floor. Take the top of the swept range "
            "and confirm real strikes still register."
        )

    floor = noisy[0].position
    target = max(lowest.position, floor - margin)
    return target, (
        f"Ambient noise started firing the trigger at step {floor}; backing off "
        f"{margin} steps lands on {target}."
    )


def describe(position: int, series_ohms: float) -> str:
    """One line of what a step means at the R17 pads and in the preamp."""
    return (
        f"step {position:3d}/{MAX_POSITION}  "
        f"R17 ~{resistance_ohms(position, series_ohms) / 1000:6.2f} kohm  "
        f"preamp ~{preamp_feedback_ohms(position, series_ohms) / 1000:5.2f} kohm"
    )


def sweep_positions(step: int) -> List[int]:
    """Steps to visit, always including both ends of the range."""
    positions = list(range(0, MAX_POSITION + 1, step))
    if positions[-1] != MAX_POSITION:
        positions.append(MAX_POSITION)
    return positions


def _open_trigger_input(pin: int):
    """Claim the sound trigger's GATE line as an input."""
    try:
        from gpiozero import DigitalInputDevice

        from openflight.gpio_factory import ensure_lgpio_pin_factory

        ensure_lgpio_pin_factory()
        return DigitalInputDevice(pin, pull_up=False)
    except ImportError as error:
        raise RuntimeError(
            f"GPIO support unavailable ({error}). Run this on the Pi with 'uv sync'."
        ) from error
    except Exception as error:
        raise RuntimeError(
            f"Could not claim BCM{pin} ({error}). Stop the OpenFlight server first -- "
            "it holds the same trigger line."
        ) from error


def observe_step(gate, position: int, dwell_s: float, sample_hz: float = 200.0):
    """Watch the GATE line for one dwell window and return a StepObservation."""
    counter = {"edges": 0}
    gate.when_activated = lambda: counter.__setitem__("edges", counter["edges"] + 1)

    interval = 1.0 / sample_hz
    deadline = time.monotonic() + dwell_s
    samples = 0
    high = 0
    while time.monotonic() < deadline:
        samples += 1
        high += 1 if gate.value else 0
        time.sleep(interval)

    gate.when_activated = None
    return StepObservation(
        position=position,
        edges=counter["edges"],
        high_fraction=high / samples if samples else 0.0,
    )


def run_noise_floor(pot, args) -> Optional[int]:
    """Sweep the wiper and report where ambient noise starts triggering."""
    gate = _open_trigger_input(args.trigger_pin)
    try:
        print(
            f"\nWatching GATE on BCM{args.trigger_pin}. Keep the room as quiet as it "
            "gets during play, and do not hit anything.\n"
        )
        print(f"  {'step':>4}  {'R17':>9}  {'edges':>6}  {'high':>6}  verdict")
        print(f"  {'-' * 4}  {'-' * 9}  {'-' * 6}  {'-' * 6}  {'-' * 9}")

        observations = []
        for position in sweep_positions(args.sweep_step):
            pot.set_position(position)
            time.sleep(args.settle)
            observation = observe_step(gate, position, args.sweep_dwell)
            observations.append(observation)
            print(
                f"  {observation.position:>4}  "
                f"{resistance_ohms(observation.position, pot.series_ohms) / 1000:>7.2f}k  "
                f"{observation.edges:>6}  "
                f"{observation.high_fraction * 100:>5.0f}%  "
                f"{observation.verdict}"
            )

        target, explanation = recommend_position(observations, margin=args.margin)
        print(f"\n{explanation}")
        if target is not None:
            print(f"\nSuggested setting: {describe(target, pot.series_ohms)}")
            print(
                "\nThis only finds the ceiling. Now confirm the floor: set that step "
                "in Debug > Sound and check real strikes still register."
            )
        return target
    finally:
        gate.close()


def validate_args(parser, args) -> None:
    """Reject argument combinations that would fail partway through a run."""
    try:
        validate_address(args.address)
    except ValueError as error:
        parser.error(str(error))
    if args.position is not None and not 0 <= args.position <= MAX_POSITION:
        parser.error(f"--position must be within 0..{MAX_POSITION}")
    if not 1 <= args.sweep_step <= MAX_POSITION:
        parser.error(f"--sweep-step must be within 1..{MAX_POSITION}")
    if args.margin < 0:
        parser.error("--margin cannot be negative")
    if args.series_ohms < 0:
        parser.error("--series-ohms cannot be negative")
    # time.sleep raises on a negative delay, which would abort a sweep partway
    # through with a traceback instead of a usable message.
    if args.sweep_dwell < 0:
        parser.error("--sweep-dwell cannot be negative")
    if args.settle < 0:
        parser.error("--settle cannot be negative")


def main() -> None:
    """Parse arguments and run the requested bring-up mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--i2c-bus", type=int, default=1, help="I2C bus number (default: 1)")
    parser.add_argument(
        "--address",
        type=lambda value: int(value, 0),
        default=DEFAULT_ADDRESS,
        help=f"DS3502 I2C address, 0x28-0x2b (default: 0x{DEFAULT_ADDRESS:02x})",
    )
    parser.add_argument(
        "--series-ohms",
        type=float,
        default=DEFAULT_SERIES_OHMS,
        help=f"Series resistor fitted in the R17 path (default: {DEFAULT_SERIES_OHMS:.0f})",
    )
    parser.add_argument(
        "--position",
        type=int,
        default=None,
        help=f"Park the wiper at this step (0..{MAX_POSITION})",
    )
    parser.add_argument(
        "--store", action="store_true", help="Commit the parked position to the chip's EEPROM"
    )
    parser.add_argument("--sweep", action="store_true", help="Step through the whole range")
    parser.add_argument(
        "--noise-floor",
        action="store_true",
        help="Sweep while counting GATE edges to find where ambient noise triggers",
    )
    parser.add_argument(
        "--trigger-pin",
        type=int,
        default=17,
        help="BCM GPIO carrying the SEN-14262 GATE edge (default: 17)",
    )
    parser.add_argument(
        "--sweep-step",
        type=int,
        default=8,
        help=f"Steps between sweep stops, 1..{MAX_POSITION} (default: 8)",
    )
    parser.add_argument(
        "--sweep-dwell",
        type=float,
        default=2.0,
        help="Seconds to hold each sweep stop (default: 2)",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=0.3,
        help="Seconds to let the preamp settle after each step (default: 0.3)",
    )
    parser.add_argument(
        "--margin",
        type=int,
        default=DEFAULT_MARGIN_STEPS,
        help=f"Steps to back off from the noise floor (default: {DEFAULT_MARGIN_STEPS})",
    )
    args = parser.parse_args()
    validate_args(parser, args)

    pot = DS3502(bus_number=args.i2c_bus, address=args.address, series_ohms=args.series_ohms)
    suggested = None
    try:
        print(f"Opening DS3502 at 0x{args.address:02x} on i2c-{args.i2c_bus}")
        pot.open()
        print(f"  found, wiper at {describe(pot.position, pot.series_ohms)}")

        if args.sweep:
            print("\nSweeping. Put a meter across the R17 pads.\n")
            for position in sweep_positions(args.sweep_step):
                pot.set_position(position)
                print(f"  {describe(position, pot.series_ohms)}")
                time.sleep(args.sweep_dwell)

        if args.noise_floor:
            suggested = run_noise_floor(pot, args)

        if args.position is not None:
            target = args.position
        elif suggested is not None:
            target = suggested
        else:
            target = pot.position
        pot.set_position(target, store=args.store)
        print(f"\nParked at {describe(target, pot.series_ohms)}")
        if args.store:
            print("Committed to the chip's EEPROM; it will come back here after a power cycle.")
        else:
            print(
                "Not committed to EEPROM: the wiper holds while powered, but a power "
                "cycle restores the stored value. Add --store, or set it in Debug > Sound."
            )
    finally:
        pot.close()


if __name__ == "__main__":
    main()
