"""The IWR6843 shot result packet, as the firmware serialises it.

``firmware/iwr6843/l3_result.h`` defines version 1: a fixed 100-byte
little-endian record of nine metrics (m/s and radians on the wire) each with
a confidence, validity and quality flag words, the impact time and source,
the point counts and the smash factor. The firmware prints it as a hex line
after ``triggerLog result``; this module parses that into measurements the
UI can label, keeping what was measured apart from what was inferred and
never turning an invalid or implausible value into a number.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from openflight.iwr6843 import firmware_host as fw

PACKET = struct.Struct("<II9fII9fIBBBBf")
assert PACKET.size == fw.RESULT_PACKET_BYTES

ANGLE_METRICS = frozenset(
    {"vertical_launch", "horizontal_launch", "club_path", "angle_of_attack", "spin_axis"}
)
IMPACT_SOURCES = {0: "none", 1: "gate", 2: "geometry", 3: "both"}


@dataclass(frozen=True)
class Measurement:
    """One metric: SI value (m/s, degrees for angles, m for range) with its provenance."""

    name: str
    value: float | None  # None when the firmware marked it invalid
    confidence: float
    measured: bool  # by the radar, as opposed to inferred or modelled
    radial_only: bool
    implausible: bool
    fallback: bool  # the configured tee stood in for a locked ball

    @property
    def usable(self) -> bool:
        return self.value is not None and not self.implausible

    @property
    def label(self) -> str:
        """MEASURED / ESTIMATED / -, for a display that must not blur the two."""
        if self.value is None:
            return "-"
        return "MEASURED" if self.measured else "ESTIMATED"


@dataclass(frozen=True)
class ShotResultPacket:
    version: int
    shot_id: int
    metrics: dict[str, Measurement]
    quality: frozenset[str]
    impact_timestamp_us: int
    verdict: str  # invalid, partial, valid
    impact_source: str
    club_points: int
    ball_points: int
    smash: float | None

    def __getitem__(self, name: str) -> Measurement:
        return self.metrics[name]


def parse_packet(raw: bytes) -> ShotResultPacket:
    """Decode one packet; raises ValueError on a wrong size or version."""
    if len(raw) != PACKET.size:
        raise ValueError(f"shot result packet is {len(raw)} bytes, expected {PACKET.size}")
    fields = PACKET.unpack(raw)
    version, shot_id = fields[0], fields[1]
    if version != fw.RESULT_VERSION:
        raise ValueError(f"shot result version {version}, this host reads {fw.RESULT_VERSION}")
    values = fields[2:11]
    valid_flags, quality_flags = fields[11], fields[12]
    confidences = fields[13:22]
    impact_us, verdict, source, club_points, ball_points, smash = fields[22:28]
    metrics: dict[str, Measurement] = {}
    for index, name in enumerate(fw.RESULT_METRIC_NAMES):
        valid = bool(valid_flags & (1 << index))
        value = values[index]
        if valid and name in ANGLE_METRICS:
            value = math.degrees(value)
        metrics[name] = Measurement(
            name=name,
            value=value if valid else None,
            confidence=confidences[index] if valid else 0.0,
            # The per-metric flag word is not on the wire; validity is the bit
            # mask and the rest is read from the quality word where it applies.
            measured=valid and name not in ("spin_rate", "spin_axis"),
            radial_only=valid
            and name in ("ball_speed", "club_speed")
            and not (valid_flags & _angle_bits_for(name)),
            implausible=valid and not _plausible(name, quality_flags),
            fallback=valid and not (quality_flags & fw.QUALITY_FLAGS["ball_locked"]),
        )
    return ShotResultPacket(
        version=version,
        shot_id=shot_id,
        metrics=metrics,
        quality=frozenset(name for name, bit in fw.QUALITY_FLAGS.items() if quality_flags & bit),
        impact_timestamp_us=impact_us,
        verdict=fw.RESULT_VERDICT_NAMES[verdict] if verdict < 3 else "?",
        impact_source=IMPACT_SOURCES.get(source, "?"),
        club_points=club_points,
        ball_points=ball_points,
        smash=smash if smash > 0.0 else None,
    )


def _angle_bits_for(name: str) -> int:
    names = fw.RESULT_METRIC_NAMES
    if name == "ball_speed":
        return (1 << names.index("vertical_launch")) | (1 << names.index("horizontal_launch"))
    return (1 << names.index("club_path")) | (1 << names.index("angle_of_attack"))


def _plausible(name: str, quality_flags: int) -> bool:
    if name in ("ball_speed", "club_speed"):
        return bool(quality_flags & fw.QUALITY_FLAGS["speeds_plausible"])
    if name in ANGLE_METRICS:
        return bool(quality_flags & fw.QUALITY_FLAGS["angles_plausible"])
    return True


def parse_hex(text: str) -> ShotResultPacket:
    """The ``packet <hex>`` line ``triggerLog result`` prints, or the bare hex."""
    token = text.strip().split()[-1] if text.strip() else ""
    try:
        raw = bytes.fromhex(token)
    except ValueError as error:
        raise ValueError(f"shot result hex is not hex: {token[:40]!r}") from error
    return parse_packet(raw)


def parse_result_reply(reply: str) -> ShotResultPacket | None:
    """The whole ``triggerLog result`` reply; None when no packet line is present.

    The firmware prints the 200 hex characters as ``packet <first half>`` and
    ``packet+ <second half>`` because one CLI line cannot carry them all.
    """
    halves: list[str] = []
    for line in reply.splitlines():
        stripped = line.strip()
        if stripped.startswith("packet ") or stripped.startswith("packet+ "):
            halves.append(stripped.split(maxsplit=1)[1])
    if not halves:
        return None
    return parse_hex("".join(halves))


__all__ = [
    "ANGLE_METRICS",
    "PACKET",
    "Measurement",
    "ShotResultPacket",
    "parse_hex",
    "parse_packet",
    "parse_result_reply",
]
