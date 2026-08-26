#!/usr/bin/env python3
"""Bench-test the X9C104 digital pot fitted to the SEN-14262 R17 pads.

Three modes, in the order you want them during bring-up:

``--sweep``
    Steps the wiper across its range so you can watch the resistance change on
    a multimeter across the R17 pads. Proves the pot is wired and stepping.

``--noise-floor``
    Steps the wiper and, at each tap, counts `GATE` edges on the sound
    trigger's GPIO. Run it in the room you actually hit in, while it is quiet:
    the tap where edges start appearing is where ambient noise begins firing
    the trigger, so the usable setting sits below it. Proves sensitivity really
    moved, which the meter alone cannot tell you.

``--position N``
    Parks the wiper at one tap.

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

    # Find where ambient noise starts triggering, in a quiet room
    uv run python scripts/hardware-test/test_x9c104.py --noise-floor

    # Park the wiper at one tap
    uv run python scripts/hardware-test/test_x9c104.py --position 46

Stop the OpenFlight server first: it holds the same GPIO lines.
"""

import argparse
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from openflight.sensitivity import (
    DEFAULT_POSITION,
    MAX_POSITION,
    X9C104,
    preamp_feedback_ohms,
    resistance_ohms,
)

# Verdicts for one dwell window on the trigger line.
QUIET = "quiet"
ACTIVE = "active"
SATURATED = "saturated"

# Above this share of the window spent high, the GATE is effectively stuck on
# rather than pulsing. Counting edges alone would call that "quiet" -- there are
# no transitions -- which is exactly backwards, and it is the classic
# over-gain failure the R17 resistor exists to fix.
SATURATED_HIGH_FRACTION = 0.9

# Taps to back off from the first noisy one. ~1 kohm per tap, so this is a
# ~5 kohm margin, comfortably more than the step size without giving up much
# detection headroom.
DEFAULT_MARGIN_TAPS = 5


@dataclass
class TapObservation:
    """What the trigger line did while the wiper sat at one tap."""

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
    observations: List[TapObservation], *, margin: int = DEFAULT_MARGIN_TAPS
) -> Tuple[Optional[int], str]:
    """Suggest a tap from a quiet-room sweep, with the reasoning.

    Returns ``(position, explanation)``; ``position`` is None when the sweep
    found nothing usable, in which case the explanation says why.
    """
    if not observations:
        return None, "No taps were sampled."

    ordered = sorted(observations, key=lambda item: item.position)
    lowest = ordered[0]
    if lowest.verdict != QUIET:
        return None, (
            f"Even the least sensitive tap ({lowest.position}) saw the trigger "
            f"{lowest.verdict}. That is not a gain problem: check the GATE wiring, "
            "the R17 pad joints, and whether the room is genuinely quiet."
        )

    noisy = [item for item in ordered if item.verdict != QUIET]
    if not noisy:
        top = ordered[-1]
        return top.position, (
            f"Nothing fired the trigger anywhere up to tap {top.position}, so this "
            "room never reached the noise floor. Take the top of the swept range "
            "and confirm real strikes still register."
        )

    floor = noisy[0].position
    target = max(lowest.position, floor - margin)
    return target, (
        f"Ambient noise started firing the trigger at tap {floor}; backing off "
        f"{margin} taps lands on {target}."
    )


def describe(position: int) -> str:
    """One line of what a tap means at the R17 pads and in the preamp."""
    return (
        f"position {position:2d}/{MAX_POSITION}  "
        f"R17 ~{resistance_ohms(position) / 1000:6.1f} kohm  "
        f"preamp ~{preamp_feedback_ohms(position) / 1000:5.1f} kohm"
    )


def sweep_positions(step: int) -> List[int]:
    """Taps to visit, always including both ends of the range."""
    positions = list(range(0, MAX_POSITION + 1, step))
    if positions[-1] != MAX_POSITION:
        positions.append(MAX_POSITION)
    return positions


def _open_trigger_input(pin: int):
    """Claim the sound trigger's GATE line as an input.

    Raises:
        RuntimeError: if gpiozero is missing or the line cannot be claimed --
            both actionable setup problems rather than something to work around.
    """
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


def observe_tap(gate, position: int, dwell_s: float, sample_hz: float = 200.0):
    """Watch the GATE line for one dwell window and return a TapObservation."""
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
    return TapObservation(
        position=position,
        edges=counter["edges"],
        high_fraction=high / samples if samples else 0.0,
    )


def run_noise_floor(pot, args) -> None:
    """Sweep the wiper and report where ambient noise starts triggering."""
    gate = _open_trigger_input(args.trigger_pin)
    try:
        print(
            f"\nWatching GATE on BCM{args.trigger_pin}. Keep the room as quiet as it "
            f"gets during play, and do not hit anything.\n"
        )
        print(f"  {'tap':>3}  {'R17':>9}  {'edges':>6}  {'high':>6}  verdict")
        print(f"  {'-' * 3}  {'-' * 9}  {'-' * 6}  {'-' * 6}  {'-' * 9}")

        observations = []
        for position in sweep_positions(args.sweep_step):
            pot.set_position(position)
            # Let the preamp settle before counting, so stepping artefacts and
            # the tail of the previous window do not land in this one.
            time.sleep(args.settle)
            observation = observe_tap(gate, position, args.sweep_dwell)
            observations.append(observation)
            print(
                f"  {observation.position:>3}  "
                f"{resistance_ohms(observation.position) / 1000:>7.1f}k  "
                f"{observation.edges:>6}  "
                f"{observation.high_fraction * 100:>5.0f}%  "
                f"{observation.verdict}"
            )

        target, explanation = recommend_position(observations, margin=args.margin)
        print(f"\n{explanation}")
        if target is not None:
            print(f"\nSuggested setting: {describe(target)}")
            print(
                "\nThis only finds the ceiling. Now confirm the floor: set that tap "
                "in Debug > Tuning and check real strikes still register."
            )
        return target
    finally:
        gate.close()


def main() -> None:
    """Parse arguments and run the requested bring-up mode."""
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
        "--sweep-step", type=int, default=10, help="Taps between sweep stops (default: 10)"
    )
    parser.add_argument(
        "--sweep-dwell", type=float, default=2.0, help="Seconds to hold each sweep stop"
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
        default=DEFAULT_MARGIN_TAPS,
        help=f"Taps to back off from the noise floor (default: {DEFAULT_MARGIN_TAPS})",
    )
    args = parser.parse_args()

    if args.position is not None and not 0 <= args.position <= MAX_POSITION:
        parser.error(f"--position must be within 0..{MAX_POSITION}")
    if args.sweep_step < 1:
        parser.error("--sweep-step must be at least 1")
    if args.margin < 0:
        parser.error("--margin cannot be negative")
    if args.noise_floor and args.trigger_pin in (args.cs_pin, args.inc_pin, args.ud_pin):
        parser.error("--trigger-pin cannot be one of the digipot's own control lines")

    pot = X9C104(cs_pin=args.cs_pin, inc_pin=args.inc_pin, ud_pin=args.ud_pin)
    suggested = None
    try:
        print(f"Claiming CS=BCM{args.cs_pin}, INC=BCM{args.inc_pin}, U/D=BCM{args.ud_pin}")
        pot.open()

        # The chip has no readback, so the only way to know where the wiper is
        # is to drive it against the RL end first.
        print("Calibrating: driving the wiper to the RL end (position 0)...")
        pot.calibrate()
        print(f"  {describe(0)}")

        if args.sweep:
            print("\nSweeping. Put a meter across the R17 pads.\n")
            for position in sweep_positions(args.sweep_step):
                pot.set_position(position)
                print(f"  {describe(position)}")
                time.sleep(args.sweep_dwell)

        if args.noise_floor:
            suggested = run_noise_floor(pot, args)

        if args.position is not None:
            target = args.position
        elif suggested is not None:
            target = suggested
        else:
            target = DEFAULT_POSITION
        pot.set_position(target)
        print(f"\nParked at {describe(target)}")
        print(
            "\nNothing was written to the chip's non-volatile memory, so a power "
            "cycle restores whatever it held before. To make a setting stick, set "
            "it in Debug > Tuning with the server running."
        )
    finally:
        pot.close()


if __name__ == "__main__":
    main()
