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
    Parks the wiper at one tap. Add ``--hold`` to keep it there: the position
    only lasts as long as something drives the control lines, so without it the
    wiper walks away the moment this script exits.

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

    # Finer and slower: every tap, five seconds each (~8 minutes)
    uv run python scripts/hardware-test/test_x9c104.py --sweep --sweep-step 1 --sweep-dwell 5

    # Park at one tap and HOLD it while you measure (see --hold below)
    uv run python scripts/hardware-test/test_x9c104.py --position 46 --hold

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


def validate_args(parser, args) -> None:
    """Reject argument combinations that would fail partway through a run."""
    if args.position is not None and not 0 <= args.position <= MAX_POSITION:
        parser.error(f"--position must be within 0..{MAX_POSITION}")
    if not 1 <= args.sweep_step <= MAX_POSITION:
        parser.error(f"--sweep-step must be within 1..{MAX_POSITION}")
    if args.margin < 0:
        parser.error("--margin cannot be negative")
    # time.sleep raises on a negative delay, which would abort a sweep partway
    # through with a traceback instead of a usable message.
    if args.sweep_dwell < 0:
        parser.error("--sweep-dwell cannot be negative")
    if args.settle < 0:
        parser.error("--settle cannot be negative")
    if args.noise_floor and args.trigger_pin in (args.cs_pin, args.inc_pin, args.ud_pin):
        parser.error("--trigger-pin cannot be one of the digipot's own control lines")


def hold_after_parking(hold: bool) -> None:
    """Keep the control lines driven, or explain why the wiper will not stay.

    Releasing the GPIOs is what makes a parked position evaporate: BCM 9-27
    default to a pull-down, so once this process exits and the lines float, CS
    is pulled low -- selecting the chip -- while INC falls with it. That falling
    edge is a step command, and the now-selected chip will keep taking steps
    from any noise on the line. The wiper walks away from wherever it was
    parked, usually toward RL.

    Nothing in software can outlive the process holding those lines, so the
    options are to stay alive while you measure, or to fit the pull-up on CS
    that the wiring guide describes.
    """
    if hold:
        print(
            "\nHolding: the lines stay driven, so the wiper stays put. "
            "Measure now, then press Enter to release."
        )
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print()
    else:
        print(
            "\nNOTE: this position will NOT survive the script exiting. Releasing "
            "the GPIOs lets CS fall low and the wiper steps away from it. Re-run "
            "with --hold to keep it parked while you measure, or fit the pull-up "
            "on CS described in docs/sound-trigger-wiring.md."
        )
    print(
        "\nNothing was written to the chip's non-volatile memory, so a power "
        "cycle restores whatever it held before. To make a setting stick, set it "
        "in Debug > Sound with the server running -- the server holds the lines "
        "for as long as it runs."
    )


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
        "--sweep-step",
        type=int,
        default=10,
        help=f"Taps between sweep stops, 1..{MAX_POSITION}; 1 visits every tap (default: 10)",
    )
    parser.add_argument(
        "--sweep-dwell",
        type=float,
        default=2.0,
        help="Seconds to hold each sweep stop (default: 2). Raise it to read a meter",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=0.3,
        help="Seconds to let the preamp settle after each step (default: 0.3)",
    )
    parser.add_argument(
        "--hold",
        action="store_true",
        help="Keep the control lines driven after parking, until you press Enter, "
        "so the wiper stays put while you measure",
    )
    parser.add_argument(
        "--margin",
        type=int,
        default=DEFAULT_MARGIN_TAPS,
        help=f"Taps to back off from the noise floor (default: {DEFAULT_MARGIN_TAPS})",
    )
    args = parser.parse_args()
    validate_args(parser, args)

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
            print(
                "\nSweeping. Put a meter across the two pot nodes.\n"
                "  Pot alone (not yet on the pads): compare the R17 column.\n"
                "  Soldered across R17: compare the preamp column -- the board's\n"
                "  own 100k R3 is in parallel, so it tops out near 50k, not 100k.\n"
            )
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
        hold_after_parking(args.hold)
    finally:
        pot.close()


if __name__ == "__main__":
    main()
