#!/usr/bin/env python3
"""Swing the IWR6843 self-trigger and print what the frozen ring contains.

Stop the kiosk first. It owns this UART, so a swing there cannot show up
here, and this script cannot show up there.

The script arms ``triggerCfg``, prints each detector phase as it changes,
and polls ``stats`` while the phase sits still so tee power stays visible.
On ``Triggered`` (or ``latched=1``) it reads the frozen ring, replays the
ball-leave detector, and prints PASS or FAIL. Ctrl+C stops.

    uv run python scripts/iwr6843/swing_trigger.py
    uv run python scripts/iwr6843/swing_trigger.py --port COM5 --tee-m 1.575
"""

from __future__ import annotations

import argparse
import time

import numpy as np

from openflight.iwr6843.calibration import DEFAULT_TEE_RANGE_M
from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.monitor import tee_local_bin
from openflight.iwr6843.self_trigger import BallLeaveDetector, TriggerObservation, replay_dump
from openflight.iwr6843.sparse import SparsePlan

_DEFAULT_CFG = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"
_QUIET_POLL_S = 2.0


def parse_trig(line: str) -> dict[str, str] | None:
    """Fields from a ``trig phase=...`` debug or stats line."""
    text = line.strip()
    marker = text.find("trig ")
    if marker < 0 or "phase=" not in text[marker:]:
        return None
    fields: dict[str, str] = {}
    for token in text[marker + len("trig ") :].split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        fields[key] = value
    if "phase" not in fields or "tee" not in fields:
        return None
    return fields


def is_latched(fields: dict[str, str] | None) -> bool:
    """True when this line says the ring is frozen.

    ``phase=fired`` with ``latched=0`` is the leftover phase after the ring
    was released. Treating that as a new swing would freeze the live ring.
    """
    if not fields:
        return False
    if fields.get("latched") == "1":
        return True
    return fields.get("phase") == "fired" and "latched" not in fields


def format_status(fields: dict[str, str], level: float) -> str:
    """One live detector line."""
    try:
        tee = float(fields["tee"])
    except ValueError:
        tee = float("nan")
    occupied = "ball" if tee >= level else "empty"
    parts = [
        f"{fields['phase']:<12} tee={fields['tee']}  level={level:g}  {occupied}",
    ]
    for key in ("approach", "peak", "latched", "enabled"):
        if key in fields:
            parts.append(f"{key}={fields[key]}")
    return "  ".join(parts)


def replay_loop0(
    power: np.ndarray,
    n_loops: int,
    tee_bin: int,
    level: float,
    hits: int,
    frame_bins: list[int] | None = None,
) -> list[TriggerObservation]:
    """Replay loop-0 residual power, the same probe the firmware uses."""
    rows, bins = np.asarray(power).shape
    if n_loops < 1 or rows % n_loops:
        raise ValueError(f"power rows {rows} are not a multiple of {n_loops} loops")
    loop0 = np.asarray(power).reshape(rows // n_loops, n_loops, bins)[:, 0, :]
    detector = BallLeaveDetector(level=level, hits=hits)
    observations: list[TriggerObservation] = []
    for frame, row in enumerate(loop0):
        count = bins if frame_bins is None else frame_bins[frame]
        tee_local = tee_bin if 0 <= tee_bin < count else None
        observations.append(detector.step(frame, row, tee_local, count))
    return observations


def format_swing(observations: list[TriggerObservation]) -> str:
    """Phase changes, then whether the replay latched."""
    if not observations:
        return "  no frames in the frozen ring"
    lines = []
    previous = None
    for obs in observations:
        if obs.phase == previous:
            continue
        lines.append(
            f"  {obs.phase:<12} frame={obs.frame}  tee={obs.tee:.0f}  "
            f"approach={obs.approach:.0f}  peak={obs.peak_bin}"
        )
        previous = obs.phase
    fired = next((obs for obs in observations if obs.fired), None)
    if fired is None:
        lines.append(f"  FAIL  replay never fired (last {observations[-1].phase})")
    else:
        lines.append(f"  PASS  replay fired at frame {fired.frame}")
    return "\n".join(lines)


def _read_power(radar: IWR6843Radar):
    """Power map from ``l3sparse``, or None when that command is refused.

    The map arrives before any complex cells. An empty cell request releases
    the ring; assembling that empty reply has no noise sample, which the
    driver rejects after the firmware has already re-armed.
    """
    held: dict = {}

    def plan(summary):
        held["summary"] = summary
        return SparsePlan(cells=())

    try:
        capture = radar.read_sparse(plan)
    except Exception:
        if "summary" not in held:
            raise
        return held["summary"]
    if capture is None:
        return None
    return held.get("summary")


def _read_swing(radar: IWR6843Radar, tee_bin: int, tee_m: float, level: float, hits: int):
    summary = _read_power(radar)
    if summary is not None:
        counts = [
            summary.geometry.frame_bin_count(frame) for frame in range(summary.geometry.n_frames)
        ]
        return replay_loop0(summary.power, summary.n_loops, tee_bin, level, hits, counts)
    print("  sparse read unavailable, reading the full ring...", flush=True)
    raw = radar.read_dump()
    return replay_dump(raw, tee_range_m=tee_m, level=level, hits=hits)


def _validate_swing(
    radar: IWR6843Radar,
    swing: int,
    tee_bin: int,
    tee_m: float,
    level: float,
    hits: int,
) -> bool:
    print(f"\nswing {swing}: reading frozen ring...", flush=True)
    try:
        observations = _read_swing(radar, tee_bin, tee_m, level, hits)
    except Exception as error:  # pylint: disable=broad-exception-caught
        print(f"  FAIL  {error}", flush=True)
        return False
    print(format_swing(observations), flush=True)
    health = radar.stats()
    for line in health.splitlines():
        fields = parse_trig(line)
        if fields is None:
            continue
        print(f"  after {format_status(fields, level)}", flush=True)
        if is_latched(fields):
            print("  FAIL  ring still latched after the read", flush=True)
            return False
        break
    return any(obs.fired for obs in observations)


def _poll_status(radar: IWR6843Radar) -> dict[str, str] | None:
    health = radar.stats()
    found = None
    for line in health.splitlines():
        fields = parse_trig(line)
        if fields is not None:
            found = fields
    return found


def _consume_line(line: str, level: float) -> dict[str, str] | None:
    if not line or line == "Done" or line.endswith(":/>"):
        return None
    fields = parse_trig(line)
    if fields is not None and not is_latched(fields):
        print(format_status(fields, level), flush=True)
    return fields


def watch(
    radar: IWR6843Radar,
    tee_bin: int,
    tee_m: float,
    level: float,
    hits: int,
) -> tuple[int, int]:
    """Print phase changes until Ctrl+C. Returns (fired, swings)."""
    pending = b""
    last_print = time.monotonic()
    swings = 0
    fired = 0
    try:
        while True:
            waiting = radar.ser.in_waiting
            chunk = radar.ser.read(waiting or 1)
            now = time.monotonic()
            if chunk:
                pending += chunk
                if len(pending) > 8192 and b"\n" not in pending:
                    pending = b""
                    print("  dropped a non-text burst", flush=True)
                    continue
                while b"\n" in pending:
                    raw, pending = pending.split(b"\n", 1)
                    line = raw.decode(errors="replace").strip()
                    if "Triggered" in line:
                        pending = b""
                        print("  Triggered", flush=True)
                        swings += 1
                        if _validate_swing(radar, swings, tee_bin, tee_m, level, hits):
                            fired += 1
                        last_print = time.monotonic()
                        print("\nwatching. swing when ready.", flush=True)
                        break
                    fields = _consume_line(line, level)
                    if is_latched(fields):
                        print(format_status(fields, level), flush=True)
                        pending = b""
                        swings += 1
                        if _validate_swing(radar, swings, tee_bin, tee_m, level, hits):
                            fired += 1
                        last_print = time.monotonic()
                        print("\nwatching. swing when ready.", flush=True)
                        break
                    if fields is not None:
                        last_print = time.monotonic()
                continue
            if pending or now - last_print < _QUIET_POLL_S:
                continue
            fields = _poll_status(radar)
            last_print = time.monotonic()
            if is_latched(fields):
                swings += 1
                if _validate_swing(radar, swings, tee_bin, tee_m, level, hits):
                    fired += 1
                print("\nwatching. swing when ready.", flush=True)
                continue
            if fields is not None:
                print(format_status(fields, level), flush=True)
    except KeyboardInterrupt:
        print(f"\n{fired}/{swings} swings replayed as fired")
    return fired, swings


def _arm(radar: IWR6843Radar, config: str, tee_bin: int, level: float, hits: int) -> None:
    radar.send_config(config)
    reply = radar.cmd("debugCfg 1")
    if "Done" not in reply:
        raise SystemExit(f"debugCfg rejected: {reply.strip()}")
    for line in reply.splitlines():
        fields = parse_trig(line)
        if fields is not None:
            print(format_status(fields, level), flush=True)
    command = f"triggerCfg {tee_bin} {level:g} {hits}"
    reply = radar.cmd(command)
    if "Done" not in reply:
        raise SystemExit(f"triggerCfg rejected: {reply.strip()}")
    print(
        f"armed {command}. Ball on the tee should read 'ball' / watching. Ctrl+C to stop.",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=None, help="IWR6843 CLI port (COMx on Windows)")
    parser.add_argument("--config", default=_DEFAULT_CFG)
    parser.add_argument("--tee-m", type=float, default=DEFAULT_TEE_RANGE_M)
    parser.add_argument("--level", type=float, default=1000.0)
    parser.add_argument("--hits", type=int, default=2)
    args = parser.parse_args()

    tee_bin = tee_local_bin(args.tee_m, args.config)
    radar = IWR6843Radar(port=args.port)
    print(f"IWR6843 on {radar.port}. Stop the kiosk before swinging.", flush=True)
    try:
        _arm(radar, args.config, tee_bin, args.level, args.hits)
        watch(radar, tee_bin, args.tee_m, args.level, args.hits)
    finally:
        try:
            radar.cmd("debugCfg 0", window=0.5)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        radar.close()


if __name__ == "__main__":
    main()
