"""How a swing zone treats labelled recordings: club points near impact
should stay inside it, everything else the club track took should not.

Each reviewed recording is replayed at the kiosk's trigger settings with this
board's calibration, and every club-track point up to the fire is sorted:

- ``impact``: a point on the labelled club within IMPACT_FRAMES of launch,
  the points the trigger fires on, which the zone must keep;
- ``club``: a point on the labelled club earlier in the approach;
- ``stray``: any other point (the body, a hand, clutter), which the zone
  should refuse.

A capture labelled empty (no ball, no club) has only stray points.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

from openflight.iwr6843 import firmware_replay as fr, label_scoring as ls
from openflight.iwr6843.board_calibration import BoardCalibration
from openflight.iwr6843.labels import Labels
from openflight.iwr6843.monitor import SELF_TRIGGER_TEE_LEAD_BINS
from openflight.iwr6843.self_trigger import FIRMWARE_TRIGGER_DEFAULT_SNR, TEE_BAND_DEFAULT_BINS
from openflight.iwr6843.swing_zone import SwingZone, ZoneVerdict, check_points, tee_forward_m
from openflight.iwr6843.tracking import RANGE_SPAN_M

KINDS = ("impact", "club", "stray")
# A labelled club point this many frames before launch (or on it) is impact.
IMPACT_FRAMES = 4
# A track point within this many bins of a club label on its frame is the club.
CLUB_MATCH_BINS = 1.5
BALL_HEIGHT_M = 0.04


@dataclass
class Tally:
    """Points of one kind: how many, and how many the zone kept."""

    points: int = 0
    inside: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def add(self, verdict: ZoneVerdict) -> None:
        """Count one point and the limits it broke."""
        self.points += 1
        self.inside += verdict.inside
        for reason in verdict.reasons:
            self.reasons[reason] = self.reasons.get(reason, 0) + 1

    @property
    def share_inside(self) -> float | None:
        """The share kept; None without points."""
        return self.inside / self.points if self.points else None


def point_kind(point: fr.PointSummary, labels: Labels) -> str:
    """impact, club or stray, from the labels on the point's frame."""
    on_club = any(
        c.frame == point.frame and abs(c.range_bin - point.range_bin) <= CLUB_MATCH_BINS
        for c in labels.club
    )
    if not on_club:
        return "stray"
    if labels.ball and labels.ball[0].frame - IMPACT_FRAMES <= point.frame <= labels.ball[0].frame:
        return "impact"
    return "club"


def tally_recording(
    points: list[fr.PointSummary],
    verdicts: list[ZoneVerdict],
    labels: Labels,
    fired_frame: int | None,
) -> dict[str, Tally]:
    """Sort a replay's points up to its fire (all of them without one)."""
    tallies = {kind: Tally() for kind in KINDS}
    for point, verdict in zip(points, verdicts, strict=True):
        if fired_frame is not None and point.frame > fired_frame:
            continue
        tallies[point_kind(point, labels)].add(verdict)
    return tallies


def kiosk_config(
    config: fr.ReplayConfig, ball_bin: int, board: BoardCalibration
) -> fr.ReplayConfig:
    """What the kiosk sends for a ball at ``ball_bin``, with this board's calibration."""
    return replace(
        config,
        tee_bin=ball_bin - SELF_TRIGGER_TEE_LEAD_BINS,
        dest_bin=None,
        snr=FIRMWARE_TRIGGER_DEFAULT_SNR,
        band_bins=TEE_BAND_DEFAULT_BINS,
        post_from_frame=None,
        overrides={},
        **board.replay_overrides(),
    )


@dataclass
class RecordingResult:
    """One recording's tallies by kind."""

    name: str
    tallies: dict[str, Tally]


def run_report(
    directories: Iterable[Path], zone: SwingZone, board: BoardCalibration, *, lib=None
) -> list[RecordingResult]:
    """Every reviewed recording in ``directories`` through the zone. A
    folder's empty captures sit at the median ball bin of its swings."""
    lib = lib or fr._default_library()  # pylint: disable=protected-access
    bin_m = RANGE_SPAN_M / fr.DEFAULT_FFT_SIZE
    results = []
    for directory in directories:
        recordings = ls.reviewed_recordings(Path(directory))
        swing_bins = sorted(round(lab.ball[0].range_bin) for _, _, lab in recordings if lab.ball)
        median_bin = swing_bins[len(swing_bins) // 2] if swing_bins else None
        for path, config, labels in recordings:
            if labels.ball:
                ball_bin = int(round(labels.ball[0].range_bin))
            elif not labels.club and median_bin is not None:
                ball_bin = median_bin
            else:
                continue
            result = fr.replay_dump(
                path.read_bytes(), kiosk_config(config, ball_bin, board), lib=lib
            )
            tee_x = tee_forward_m(ball_bin * bin_m, board.radar_height_m, BALL_HEIGHT_M)
            cfg = zone.cfg(lib, tee_x, board.radar_height_m)
            points = list(result.points)
            verdicts = check_points(lib, cfg, points)
            results.append(
                RecordingResult(
                    path.name, tally_recording(points, verdicts, labels, result.fired_frame)
                )
            )
    return results


def totals(results: Iterable[RecordingResult]) -> dict[str, Tally]:
    """Every recording's tallies added up by kind."""
    out = {kind: Tally() for kind in KINDS}
    for result in results:
        for kind, tally in result.tallies.items():
            out[kind].points += tally.points
            out[kind].inside += tally.inside
            for reason, n in tally.reasons.items():
                out[kind].reasons[reason] = out[kind].reasons.get(reason, 0) + n
    return out


def format_totals(results: list[RecordingResult]) -> str:
    """One line per kind: the share the zone kept and why it refused the rest."""
    lines = [f"{len(results)} recordings", "kind     points  inside  refused because"]
    for kind, tally in totals(results).items():
        share = "   -  " if tally.share_inside is None else f"{tally.share_inside * 100:5.0f}%"
        why = ", ".join(f"{r} {n}" for r, n in sorted(tally.reasons.items(), key=lambda kv: -kv[1]))
        lines.append(f"{kind:8} {tally.points:6d}  {share}  {why}")
    return "\n".join(lines)
