#!/usr/bin/env python3
"""Bench-test closed-loop auto gain against a consistent noise source.

Parks the digipot at a quiet step, samples the SEN-14262 ``ENVELOPE`` through
the ADS1115, and feeds those peaks into the same ``AutoGainController`` the
server uses. A constant source (phone white noise, a fan, a speaker aimed at
the microphone) stands in for shots so the loop has to raise gain into band.

The default target band is narrower than the server's 60-80% so a DS3502
behind 33 kOhm still has authority. Stop the OpenFlight server first: it holds
the same I2C devices.

Usage:
    uv run python scripts/hardware-test/test_autogain.py
    uv run python scripts/hardware-test/test_autogain.py --watch
    uv run python scripts/hardware-test/test_autogain.py --hits
    uv run python scripts/hardware-test/test_autogain.py --device ds3502
    uv run python scripts/hardware-test/test_autogain.py --start-position 0 --shots 24
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from openflight.sensitivity import (
    ADS1115,
    DEFAULT_DEVICE,
    DEVICES,
    AutoGainController,
    EnvelopeMonitor,
    has_authority,
)
from openflight.sensitivity.ads1115 import validate_address as validate_envelope_address
from openflight.sensitivity.autogain import AT_LIMIT

DEFAULT_HIT_THRESHOLD_VOLTS = 0.05


@dataclass(frozen=True)
class HitPeak:
    """The loudest envelope sample in one excursion above the hit threshold."""

    volts: float
    fraction: float
    samples: int


class HitTracker:
    """Collect one peak per time the envelope stays above ``threshold_volts``."""

    def __init__(self, threshold_volts: float = DEFAULT_HIT_THRESHOLD_VOLTS):
        self.threshold_volts = threshold_volts
        self._active = False
        self._peak_volts = 0.0
        self._peak_fraction = 0.0
        self._samples = 0

    def feed(self, volts: Optional[float], fraction: float = 0.0) -> Optional[HitPeak]:
        """Fold in one sample. Returns a peak when the signal falls back through the threshold."""
        if volts is None:
            return None
        if volts > self.threshold_volts:
            if not self._active:
                self._active = True
                self._peak_volts = volts
                self._peak_fraction = fraction
                self._samples = 1
            else:
                self._samples += 1
                if volts > self._peak_volts:
                    self._peak_volts = volts
                    self._peak_fraction = fraction
            return None
        if not self._active:
            return None
        self._active = False
        peak = HitPeak(volts=self._peak_volts, fraction=self._peak_fraction, samples=self._samples)
        self._samples = 0
        return peak

    def flush(self) -> Optional[HitPeak]:
        """Return the in-progress peak, if any, so Ctrl+C does not drop the last hit."""
        if not self._active:
            return None
        self._active = False
        peak = HitPeak(
            volts=self._peak_volts, fraction=self._peak_fraction, samples=self._samples
        )
        self._samples = 0
        return peak


# Narrower than the server default so the DS3502 + 33 kOhm trim can still move.
DEFAULT_TARGET_LOW = 0.68
DEFAULT_TARGET_HIGH = 0.76
DEFAULT_DETECTOR_VOLTS = 3.3
DEFAULT_SHOTS = 20
DEFAULT_INTERVAL_S = 0.4
DEFAULT_SETTLE_S = 0.3
QUIET_FRACTION = 0.05
MIN_SCALE_RATIO = 1.05
FOLLOW_RATIO = 1.05
NO_SAMPLE = "no_sample"


@dataclass(frozen=True)
class RoundRecord:
    """One fake-shot observation and the controller's decision."""

    position: int
    next_position: int
    action: str
    fraction: Optional[float]
    reason: str


def envelope_responds_with_reason(
    low_fraction: float,
    high_fraction: float,
    *,
    quiet_fraction: float = QUIET_FRACTION,
    min_ratio: float = MIN_SCALE_RATIO,
    detector_volts: float = DEFAULT_DETECTOR_VOLTS,
) -> Tuple[bool, str]:
    """Explain whether envelope amplitude tracks wiper direction."""
    if max(low_fraction, high_fraction) < quiet_fraction:
        low_v = low_fraction * detector_volts
        high_v = high_fraction * detector_volts
        return False, (
            f"Envelope stayed near zero at both ends of the pot "
            f"({low_fraction:.0%} / {high_fraction:.0%}, {low_v:.2f} V / {high_v:.2f} V). "
            "The ADS1115 is returning an idle reading, not a missing device. A quiet "
            "SEN-14262 ENVELOPE and a floating A0 both sit near 0 V. Run with --watch "
            "and clap at the microphone: if volts do not jump, check ENVELOPE is on A0 "
            "and the detector is powered."
        )
    if high_fraction <= low_fraction * min_ratio:
        return False, (
            "Envelope does not track gain: the louder wiper did not read louder. "
            "Check ENVELOPE is on A0 and the pot is in the R17 path."
        )
    return True, "Envelope tracks gain."


def envelope_responds(low_fraction: float, high_fraction: float, **kwargs) -> bool:
    """True when a more sensitive wiper reads a louder envelope."""
    return envelope_responds_with_reason(low_fraction, high_fraction, **kwargs)[0]


def judge_run(
    records: List[RoundRecord],
    *,
    target_low: float = DEFAULT_TARGET_LOW,
    target_high: float = DEFAULT_TARGET_HIGH,
    quiet_fraction: float = QUIET_FRACTION,
    follow_ratio: float = FOLLOW_RATIO,
) -> Tuple[bool, str]:
    """Decide whether a collected run proved the closed loop."""
    if not records:
        return False, "No rounds were collected."

    fractions = [item.fraction for item in records if item.fraction is not None]
    if not fractions:
        return False, (
            "The ADS1115 never returned envelope samples. Stop the server, "
            "check ENVELOPE is on A0, and i2cdetect for 0x48."
        )
    if max(fractions) < quiet_fraction:
        return False, (
            f"Envelope stayed near zero ({max(fractions):.0%}). The ADS1115 is "
            "returning an idle reading, not a missing device. Run with --watch "
            "and clap at the microphone: if volts do not jump, check ENVELOPE is "
            "on A0 and the detector is powered."
        )

    moves = [item for item in records if item.next_position != item.position]
    if not moves:
        last = fractions[-1]
        if any(item.action == AT_LIMIT for item in records):
            reason = next(item.reason for item in records if item.action == AT_LIMIT)
            return False, (
                f"Wiper is at a limit and never moved. {reason} "
                "Change the series resistor, narrow the band, or change the source level."
            )
        if target_low <= last <= target_high:
            return False, (
                "Already inside the band at the start step, so the loop never "
                "had to move. Start at a quieter step (--start-position 0) or "
                "turn the source down."
            )
        return False, (
            "The loop never moved the wiper. Check the digipot is on the R17 "
            "pads and the noise is steady."
        )

    first_move = moves[0]
    later = [
        item.fraction
        for item in records[records.index(first_move) + 1 :]
        if item.fraction is not None
    ]
    if later and first_move.fraction is not None:
        after = sorted(later)[len(later) // 2]
        raised = first_move.next_position > first_move.position
        if raised and after <= first_move.fraction * follow_ratio:
            return False, (
                "Gain went up but the envelope did not. The pot may not be in "
                "the R17 path, or the source changed."
            )
        if not raised and after >= first_move.fraction / follow_ratio:
            return False, (
                "Gain went down but the envelope did not. The pot may not be in "
                "the R17 path, or the source changed."
            )

    last = records[-1]
    parked = last.next_position
    return True, (
        f"Auto gain moved the wiper from step {records[0].position} to {parked} "
        f"(envelope {fractions[0]:.0%} → {fractions[-1]:.0%})."
    )


def observe_level(
    *,
    read: Callable[[], Optional[object]],
    dwell_s: float,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    poll_s: float = 0.02,
):
    """Return the loudest envelope sample seen during ``dwell_s``."""
    deadline = now() + dwell_s
    best = None
    while now() < deadline:
        sample = read()
        if sample is not None and (best is None or sample.volts > best.volts):
            best = sample
        remaining = deadline - now()
        if remaining <= 0:
            break
        sleep(min(poll_s, remaining))
    return best


def collect_rounds(
    *,
    envelope,
    pot,
    controller: AutoGainController,
    shots: int,
    interval_s: float,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> List[RoundRecord]:
    """Feed envelope peaks to the controller as if they were shots."""
    records: List[RoundRecord] = []
    for _ in range(shots):
        sleep(interval_s)
        peak = envelope.peak_for_impact(now())
        position = pot.position
        if peak is None:
            records.append(
                RoundRecord(
                    position=position,
                    next_position=position,
                    action=NO_SAMPLE,
                    fraction=None,
                    reason="No envelope samples in the window.",
                )
            )
            continue
        decision = controller.observe(peak.fraction_of_full_scale, position, clipped=peak.clipped)
        if decision.changed:
            pot.set_position(decision.next_position, store=False)
        records.append(
            RoundRecord(
                position=position,
                next_position=decision.next_position,
                action=decision.action,
                fraction=peak.fraction_of_full_scale,
                reason=decision.reason,
            )
        )
    return records


def validate_args(parser: argparse.ArgumentParser, args, max_position: int = 127) -> None:
    """Reject argument combinations that would fail partway through a run."""
    if args.address is not None:
        try:
            DEVICES[args.device]["validate_address"](args.address)
        except ValueError as error:
            parser.error(str(error))
    try:
        validate_envelope_address(args.envelope_address)
    except ValueError as error:
        parser.error(str(error))
    if not 0 <= args.envelope_channel <= 3:
        parser.error("--envelope-channel must be within 0..3")
    if not 0 <= args.start_position <= max_position:
        parser.error(f"--start-position must be within 0..{max_position}")
    if args.shots < 1:
        parser.error("--shots must be at least 1")
    if args.interval < 0:
        parser.error("--interval cannot be negative")
    if args.settle < 0:
        parser.error("--settle cannot be negative")
    if args.series_ohms is not None and args.series_ohms < 0:
        parser.error("--series-ohms cannot be negative")
    if args.end_to_end_ohms is not None and args.end_to_end_ohms <= 0:
        parser.error("--end-to-end-ohms must be positive")
    if not 0.0 < args.target_low < args.target_high < 1.0:
        parser.error("--target-low/--target-high must satisfy 0 < low < high < 1")
    if args.detector_volts <= 0:
        parser.error("--detector-volts must be positive")
    if args.watch_seconds <= 0:
        parser.error("--watch-seconds must be positive")
    if args.threshold_volts <= 0:
        parser.error("--threshold-volts must be positive")


def _describe(pot, position: int) -> str:
    return (
        f"step {position:3d}/{pot.max_position}  "
        f"R17 ~{pot.resistance_at(position) / 1000:6.2f} kohm"
    )


def _read_peak(envelope: EnvelopeMonitor, dwell_s: float):
    """Watch the live envelope for ``dwell_s`` and keep the loudest sample."""
    return observe_level(read=envelope.latest_sample, dwell_s=dwell_s)


def _probe_scale(pot, envelope: EnvelopeMonitor, settle_s: float) -> Tuple[bool, str]:
    """Compare envelope at the two end stops before running the loop."""
    low_step = 0
    high_step = pot.max_position
    pot.set_position(low_step, store=False)
    low_peak = _read_peak(envelope, settle_s)
    pot.set_position(high_step, store=False)
    high_peak = _read_peak(envelope, settle_s)
    if low_peak is None or high_peak is None:
        return False, (
            "The ADS1115 never returned envelope samples. Stop the server, "
            "check ENVELOPE is on A0, and i2cdetect for 0x48."
        )
    low = low_peak.fraction_of_full_scale
    high = high_peak.fraction_of_full_scale
    print(
        f"  scale probe: step {low_step} → {low:.0%} ({low_peak.volts:.2f} V), "
        f"step {high_step} → {high:.0%} ({high_peak.volts:.2f} V)"
    )
    return envelope_responds_with_reason(low, high)


def main() -> int:
    """Parse arguments, run the loop, and return a process status."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--device",
        choices=tuple(DEVICES),
        default=DEFAULT_DEVICE,
        help=f"Which digipot is fitted (default: {DEFAULT_DEVICE})",
    )
    parser.add_argument("--i2c-bus", type=int, default=1, help="I2C bus number (default: 1)")
    parser.add_argument(
        "--address",
        type=lambda value: int(value, 0),
        default=None,
        help="Digipot I2C address; defaults to the selected device's own",
    )
    parser.add_argument(
        "--end-to-end-ohms",
        type=float,
        default=None,
        help="The MCP401X's own resistance (5k/10k/50k/100k); defaults to 100k",
    )
    parser.add_argument(
        "--series-ohms",
        type=float,
        default=None,
        help="Series resistor fitted in the R17 path; defaults per device",
    )
    parser.add_argument(
        "--envelope-address",
        type=lambda value: int(value, 0),
        default=0x48,
        help="ADS1115 I2C address (default: 0x48)",
    )
    parser.add_argument(
        "--envelope-channel",
        type=int,
        default=0,
        help="ADS1115 single-ended input carrying ENVELOPE (default: 0)",
    )
    parser.add_argument(
        "--detector-volts",
        type=float,
        default=DEFAULT_DETECTOR_VOLTS,
        help="SEN-14262 supply, where the envelope clips (default: 3.3)",
    )
    parser.add_argument(
        "--start-position",
        type=int,
        default=0,
        help="Wiper step to start from, usually the quiet end (default: 0)",
    )
    parser.add_argument(
        "--shots",
        type=int,
        default=DEFAULT_SHOTS,
        help=f"Fake shots to feed the controller (default: {DEFAULT_SHOTS})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help=f"Seconds between fake shots (default: {DEFAULT_INTERVAL_S})",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=DEFAULT_SETTLE_S,
        help=f"Seconds to let the preamp settle after a wiper move (default: {DEFAULT_SETTLE_S})",
    )
    parser.add_argument(
        "--target-low",
        type=float,
        default=DEFAULT_TARGET_LOW,
        help=f"Lower edge of the target band (default: {DEFAULT_TARGET_LOW})",
    )
    parser.add_argument(
        "--target-high",
        type=float,
        default=DEFAULT_TARGET_HIGH,
        help=f"Upper edge of the target band (default: {DEFAULT_TARGET_HIGH})",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Print live A0 volts so you can clap-test the ENVELOPE wire",
    )
    parser.add_argument(
        "--watch-seconds",
        type=float,
        default=20.0,
        help="How long --watch runs (default: 20)",
    )
    parser.add_argument(
        "--hits",
        action="store_true",
        help="Print ENVELOPE samples above --threshold-volts while you hit balls (Ctrl+C to stop)",
    )
    parser.add_argument(
        "--threshold-volts",
        type=float,
        default=DEFAULT_HIT_THRESHOLD_VOLTS,
        help=f"Hit-recording floor in volts (default: {DEFAULT_HIT_THRESHOLD_VOLTS})",
    )
    args = parser.parse_args()
    spec = DEVICES[args.device]
    validate_args(parser, args)
    address = spec["address"] if args.address is None else args.address
    series_ohms = spec["series_ohms"] if args.series_ohms is None else args.series_ohms
    extra = {}
    if args.end_to_end_ohms is not None and spec.get("configurable_end_to_end"):
        extra["end_to_end_ohms"] = args.end_to_end_ohms

    pot = spec["driver"](bus_number=args.i2c_bus, address=address, series_ohms=series_ohms, **extra)
    adc = ADS1115(
        bus_number=args.i2c_bus,
        address=args.envelope_address,
        channel=args.envelope_channel,
    )
    envelope = EnvelopeMonitor(
        adc,
        full_scale_volts=args.detector_volts,
        sample_interval_s=1.0 / getattr(adc, "data_rate", 860),
    )
    controller = AutoGainController(
        series_ohms=series_ohms,
        target_low=args.target_low,
        target_high=args.target_high,
        model=pot,
    )

    print(
        "Keep a consistent noise source aimed at the SEN-14262 microphone. "
        "Stop the OpenFlight server first -- it holds these I2C devices.\n"
    )
    if not has_authority(args.target_low, args.target_high, series_ohms, pot):
        print(
            "Warning: this pot/series resistor cannot span the target band. "
            "The loop may hold. Narrow --target-low/--target-high, or fit a "
            "smaller series resistor.\n"
        )

    try:
        print(f"Opening {args.device} at 0x{address:02x} on i2c-{args.i2c_bus}")
        pot.open()
        envelope.start()
        print(f"  wiper at {_describe(pot, pot.position)}")
        print(
            f"  ADS1115 at 0x{args.envelope_address:02x} A{args.envelope_channel}, "
            f"band {args.target_low:.0%}-{args.target_high:.0%}\n"
        )

        if args.watch:
            print(
                "Watching A0. Clap next to the SEN-14262 microphone. "
                "Volts should jump; a line stuck near 0.1 V is idle or floating.\n"
            )
            deadline = time.time() + args.watch_seconds
            while time.time() < deadline:
                sample = envelope.latest_sample()
                if sample is None:
                    print("  waiting for ADS1115...")
                else:
                    print(f"  {sample.volts:6.3f} V  ({sample.fraction_of_full_scale:6.1%})")
                time.sleep(0.1)
            return 0

        if args.hits:
            print(
                f"Recording hits. Each strike's peak from the ADS1115 buffer "
                f"(above {args.threshold_volts:.2f} V) prints when the pulse ends. "
                "Hit a few balls. Ctrl+C to stop.\n"
            )
            tracker = HitTracker(args.threshold_volts)
            hit_count = 0

            def _emit(peak):
                nonlocal hit_count
                if peak is None:
                    return
                hit_count += 1
                print(
                    f"hit {hit_count:3d}  {peak.volts:6.3f} V  "
                    f"({peak.fraction * 100:5.1f}%)  {peak.samples} samples",
                    flush=True,
                )

            try:
                while True:
                    sample = envelope.peak_for_impact(time.time())
                    if sample is None:
                        time.sleep(0.02)
                        continue
                    _emit(tracker.feed(sample.volts, sample.fraction_of_full_scale))
                    time.sleep(0.02)
            except KeyboardInterrupt:
                _emit(tracker.flush())
                print(f"\nStopped after {hit_count} hit(s).")
            return 0

        ok, explanation = _probe_scale(pot, envelope, max(args.settle, 1.0))
        if not ok:
            print(f"\nFAIL: {explanation}")
            return 1

        pot.set_position(args.start_position, store=False)
        time.sleep(args.settle)
        print(f"\nStarting loop at {_describe(pot, args.start_position)}\n")
        print(f"  {'shot':>4}  {'step':>4}  {'env':>6}  action  reason")
        print(f"  {'-' * 4}  {'-' * 4}  {'-' * 6}  {'-' * 6}  {'-' * 6}")

        records = collect_rounds(
            envelope=envelope,
            pot=pot,
            controller=controller,
            shots=args.shots,
            interval_s=args.interval,
        )
        for index, item in enumerate(records, start=1):
            env = "  n/a" if item.fraction is None else f"{item.fraction:5.0%}"
            print(f"  {index:>4}  {item.position:>4}  {env:>6}  {item.action:<6}  {item.reason}")

        ok, explanation = judge_run(
            records, target_low=args.target_low, target_high=args.target_high
        )
        print(f"\n{'PASS' if ok else 'FAIL'}: {explanation}")
        return 0 if ok else 1
    finally:
        envelope.stop()
        pot.close()


if __name__ == "__main__":
    sys.exit(main())
