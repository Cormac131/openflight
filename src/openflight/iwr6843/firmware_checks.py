"""Hardware checks for the IWR6843 L3-dump firmware CLI.

Everything the hardware-test script ``scripts/hardware-test/test_iwr_firmware.py``
decides lives here so it can be unit-tested against a scripted serial port.
The checks talk to the firmware through ``IWR6843Radar`` only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_INT_FIELD = re.compile(r"(\w+)=(\d+)")
_USED = re.compile(r"used=(\d+)/(\d+)")
_PLAN = re.compile(r"plan=(\d+)pre/(\d+)post")
_FORMAT = re.compile(r"format=(iq8|iq16)")
_DETECT = re.compile(r"detect dropped=(\d+) stale=(\d+)")


def parse_stats(text: str) -> dict[str, int]:
    """Every ``key=<digits>`` pair of a firmware ``stats`` response.

    Compound fields (``format=iq8``, ``plan=24pre/0post``, ``used=100/200``,
    ``calib=0x0``) partially match on their leading digits; ``parse_snapshot``
    reads those with their own patterns.
    """
    return {key: int(value) for key, value in _INT_FIELD.findall(text)}


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


@dataclass(frozen=True)
class StatsSnapshot:
    """Typed view of one ``stats`` reply; ``None`` where the image omits a field."""

    raw: str
    active: int | None = None
    frames: int | None = None
    pre_seen: int | None = None
    plan_pre: int | None = None
    plan_post: int | None = None
    freeze_req: int | None = None
    freeze_done: int | None = None
    format: str | None = None
    stride: int | None = None
    used: int | None = None
    capacity: int | None = None
    rearm_last_us: int | None = None
    rearm_max_us: int | None = None
    rearm_timed: int | None = None
    detect_dropped: int | None = None
    detect_stale: int | None = None
    phase: str | None = None
    tee: int | None = None
    latched: int | None = None
    enabled: int | None = None


def _trig_int(fields: dict[str, str] | None, key: str) -> int | None:
    if not fields or key not in fields:
        return None
    try:
        return int(float(fields[key]))
    except ValueError:
        return None


def parse_snapshot(text: str) -> StatsSnapshot:
    """Parse a whole ``stats`` reply into a ``StatsSnapshot``."""
    ints = parse_stats(text)
    used = _USED.search(text)
    plan = _PLAN.search(text)
    fmt = _FORMAT.search(text)
    detect = _DETECT.search(text)
    trig = None
    for line in text.splitlines():
        parsed = parse_trig(line)
        if parsed is not None:
            trig = parsed
    return StatsSnapshot(
        raw=text,
        active=ints.get("active"),
        frames=ints.get("frames"),
        pre_seen=ints.get("pre_seen"),
        plan_pre=int(plan.group(1)) if plan else None,
        plan_post=int(plan.group(2)) if plan else None,
        freeze_req=ints.get("freeze_req"),
        freeze_done=ints.get("freeze_done"),
        format=fmt.group(1) if fmt else None,
        stride=ints.get("stride"),
        used=int(used.group(1)) if used else None,
        capacity=int(used.group(2)) if used else None,
        rearm_last_us=ints.get("rearm_last_us"),
        rearm_max_us=ints.get("rearm_max_us"),
        rearm_timed=ints.get("rearm_timed"),
        detect_dropped=int(detect.group(1)) if detect else None,
        detect_stale=int(detect.group(2)) if detect else None,
        phase=trig.get("phase") if trig else None,
        tee=_trig_int(trig, "tee"),
        latched=_trig_int(trig, "latched"),
        enabled=_trig_int(trig, "enabled"),
    )
