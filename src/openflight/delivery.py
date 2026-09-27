"""Inferred delivery metrics: face angle, face-to-path, smash factor, and the
plausibility gates that catch bad tracking.

Radar does not see the club face; it sees the club's velocity and the
ball's launch direction. Face angle is therefore INFERRED here from the
D-plane rule of thumb that the ball starts mostly where the face points and
partly along the path: start = w * face + (1 - w) * path with w about 0.75
for irons and 0.85 for a driver (TrackMan's published ranges). Every value
this module produces is labelled inferred, never "radar measured face angle".

Smash factor is ball speed over club speed. It is a strike-quality metric
and a sanity check in one: bad tracking produces physically impossible smash,
so the plausibility gates read speeds, smash and angles together and return
the reasons a shot fails them rather than a bare boolean.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from openflight.clubs import ClubType

MPH_PER_MPS = 2.23694

# Share of the start direction the face explains (the rest is the path).
FACE_WEIGHT_DRIVER = 0.85
FACE_WEIGHT_IRON = 0.75

# Physical bounds, the same ones the firmware's l3_result.h applies.
CLUB_SPEED_MPS = (5.0, 70.0)
BALL_SPEED_MPS = (5.0, 100.0)
SMASH = (0.8, 1.6)
VERTICAL_LAUNCH_DEG = (-10.0, 60.0)
HORIZONTAL_LAUNCH_DEG = (-45.0, 45.0)
CLUB_PATH_DEG = (-30.0, 30.0)
ANGLE_OF_ATTACK_DEG = (-20.0, 20.0)
FACE_DEG = (-45.0, 45.0)


def face_weight(club: ClubType | None) -> float:
    """How much of the start line the face sets for this club: more for the
    driver and fairway woods (less loft, less gear effect on the start line)."""
    if club is not None and (club is ClubType.DRIVER or club.name.startswith("WOOD")):
        return FACE_WEIGHT_DRIVER
    return FACE_WEIGHT_IRON


@dataclass(frozen=True)
class FaceInference:
    """Face angle inferred from path and ball start; positive is open (right)."""

    face_deg: float
    face_to_path_deg: float
    weight: float  # the face share used
    source: str = "inferred"  # never "measured"

    @property
    def label(self) -> str:
        return "ESTIMATED"


def infer_face(
    club_path_deg: float, ball_start_deg: float, club: ClubType | None = None
) -> FaceInference:
    """Face from start = w * face + (1 - w) * path, solved for the face.

    Conventions follow l3_frames.h: positive is right of the target line, so
    a positive face is open for a right-hander and face-to-path positive
    means face open to the path (a fade or slice shape).
    """
    weight = face_weight(club)
    face = (ball_start_deg - (1.0 - weight) * club_path_deg) / weight
    return FaceInference(face_deg=face, face_to_path_deg=face - club_path_deg, weight=weight)


def smash_factor(ball_speed_mps: float, club_speed_mps: float) -> float | None:
    """Ball speed over club speed; None when the club speed is missing or zero."""
    if club_speed_mps is None or club_speed_mps <= 0.0 or ball_speed_mps is None:
        return None
    return ball_speed_mps / club_speed_mps


def _within(value: float | None, bounds: tuple[float, float]) -> bool:
    return value is None or bounds[0] <= value <= bounds[1]


@dataclass(frozen=True)
class Plausibility:
    """Which physical gates a shot passes; ``reasons`` names the ones it fails."""

    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.reasons


def check_plausibility(  # pylint: disable=too-many-arguments
    *,
    ball_speed_mps: float | None = None,
    club_speed_mps: float | None = None,
    vertical_launch_deg: float | None = None,
    horizontal_launch_deg: float | None = None,
    club_path_deg: float | None = None,
    angle_of_attack_deg: float | None = None,
    face_deg: float | None = None,
) -> Plausibility:
    """Every value present must sit inside its physical bounds; smash is
    checked when both speeds are present."""
    reasons: list[str] = []
    if not _within(ball_speed_mps, BALL_SPEED_MPS):
        reasons.append(f"ball speed {ball_speed_mps:.1f} m/s outside {BALL_SPEED_MPS}")
    if not _within(club_speed_mps, CLUB_SPEED_MPS):
        reasons.append(f"club speed {club_speed_mps:.1f} m/s outside {CLUB_SPEED_MPS}")
    smash = (
        smash_factor(ball_speed_mps, club_speed_mps) if ball_speed_mps and club_speed_mps else None
    )
    if smash is not None and not _within(smash, SMASH):
        reasons.append(f"smash {smash:.2f} outside {SMASH}: doubt both speeds")
    if not _within(vertical_launch_deg, VERTICAL_LAUNCH_DEG):
        reasons.append(
            f"vertical launch {vertical_launch_deg:.1f} deg outside {VERTICAL_LAUNCH_DEG}"
        )
    if not _within(horizontal_launch_deg, HORIZONTAL_LAUNCH_DEG):
        reasons.append(
            f"horizontal launch {horizontal_launch_deg:.1f} deg outside {HORIZONTAL_LAUNCH_DEG}"
        )
    if not _within(club_path_deg, CLUB_PATH_DEG):
        reasons.append(f"club path {club_path_deg:.1f} deg outside {CLUB_PATH_DEG}")
    if not _within(angle_of_attack_deg, ANGLE_OF_ATTACK_DEG):
        reasons.append(
            f"angle of attack {angle_of_attack_deg:.1f} deg outside {ANGLE_OF_ATTACK_DEG}"
        )
    if not _within(face_deg, FACE_DEG):
        reasons.append(f"face {face_deg:.1f} deg outside {FACE_DEG}")
    return Plausibility(tuple(reasons))


@dataclass(frozen=True)
class DeliveryReport:
    """Measured club delivery and ball start beside the inferred face metrics."""

    club_speed_mps: float | None
    club_path_deg: float | None
    angle_of_attack_deg: float | None
    ball_speed_mps: float | None
    horizontal_launch_deg: float | None
    face: FaceInference | None
    smash: float | None
    plausibility: Plausibility

    @property
    def club_speed_mph(self) -> float | None:
        return None if self.club_speed_mps is None else self.club_speed_mps * MPH_PER_MPS

    def lines(self) -> list[str]:
        """MEASURED first, ESTIMATED after, as the roadmap asks the UI to keep them."""
        out = ["MEASURED"]
        for name, value, unit in (
            ("club speed", self.club_speed_mph, "mph"),
            ("club path", self.club_path_deg, "deg"),
            ("angle of attack", self.angle_of_attack_deg, "deg"),
            (
                "ball speed",
                None if self.ball_speed_mps is None else self.ball_speed_mps * MPH_PER_MPS,
                "mph",
            ),
            ("start direction", self.horizontal_launch_deg, "deg"),
        ):
            out.append(f"  {name:16s} {'-' if value is None else f'{value:+.1f} {unit}'}")
        out.append("ESTIMATED")
        face = "-" if self.face is None else f"{self.face.face_deg:+.1f} deg"
        ftp = "-" if self.face is None else f"{self.face.face_to_path_deg:+.1f} deg"
        smash = "-" if self.smash is None else f"{self.smash:.2f}"
        out.append(f"  {'face angle':16s} {face}")
        out.append(f"  {'face to path':16s} {ftp}")
        out.append(f"  {'smash factor':16s} {smash}")
        if not self.plausibility.ok:
            out.append("IMPLAUSIBLE: " + "; ".join(self.plausibility.reasons))
        return out


def build_report(  # pylint: disable=too-many-arguments
    *,
    club_speed_mps: float | None,
    club_path_deg: float | None,
    angle_of_attack_deg: float | None,
    ball_speed_mps: float | None,
    horizontal_launch_deg: float | None,
    club: ClubType | None = None,
) -> DeliveryReport:
    """Everything derivable from the two trajectories, labelled and gated."""
    face = (
        infer_face(club_path_deg, horizontal_launch_deg, club)
        if club_path_deg is not None and horizontal_launch_deg is not None
        else None
    )
    smash = (
        smash_factor(ball_speed_mps, club_speed_mps)
        if ball_speed_mps is not None and club_speed_mps is not None
        else None
    )
    plausibility = check_plausibility(
        ball_speed_mps=ball_speed_mps,
        club_speed_mps=club_speed_mps,
        horizontal_launch_deg=horizontal_launch_deg,
        club_path_deg=club_path_deg,
        angle_of_attack_deg=angle_of_attack_deg,
        face_deg=None if face is None else face.face_deg,
    )
    return DeliveryReport(
        club_speed_mps=club_speed_mps,
        club_path_deg=club_path_deg,
        angle_of_attack_deg=angle_of_attack_deg,
        ball_speed_mps=ball_speed_mps,
        horizontal_launch_deg=horizontal_launch_deg,
        face=face,
        smash=smash,
        plausibility=plausibility,
    )


__all__ = [
    "ANGLE_OF_ATTACK_DEG",
    "BALL_SPEED_MPS",
    "CLUB_PATH_DEG",
    "CLUB_SPEED_MPS",
    "FACE_WEIGHT_DRIVER",
    "FACE_WEIGHT_IRON",
    "HORIZONTAL_LAUNCH_DEG",
    "SMASH",
    "VERTICAL_LAUNCH_DEG",
    "DeliveryReport",
    "FaceInference",
    "Plausibility",
    "build_report",
    "check_plausibility",
    "face_weight",
    "infer_face",
    "smash_factor",
]
