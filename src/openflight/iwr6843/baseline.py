"""Per-shot baseline records from session logs (roadmap phase 0).

Before the capture representation changes, the current firmware's numbers
are frozen as the reference: for every shot, what the ball detector, club
track, impact detector, ball track, result packet and the OPS reported.
``collect_baseline`` joins a session's ``shot_detected`` entries (OPS speeds,
spin, the onboard result) with its ``iwr6843_capture`` entries (the host
LCMF measurement, club path, capture cost) by shot number and flattens
them into one row per shot. ``write_csv`` writes the dataset the later A/B
comparisons are judged against. No algorithm runs here: it is a record of
what the board said at a known firmware SHA.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, fields
from pathlib import Path

MPS_TO_MPH = 2.23694

# The onboard result metrics, in the order the packet carries them.
ONBOARD_METRICS = (
    "ball_speed",
    "vertical_launch",
    "horizontal_launch",
    "club_speed",
    "club_path",
    "angle_of_attack",
    "spin_rate",
    "spin_axis",
    "impact_range",
)


@dataclass(frozen=True)
class BaselineRow:
    """One shot of the baseline dataset. Speeds in mph, angles in degrees."""

    session: str
    shot_number: int
    timestamp: str
    club: str | None
    capture_format: str | None
    firmware_sha: str | None
    # OPS (the reference for the two speeds)
    ops_ball_speed_mph: float | None
    ops_club_speed_mph: float | None
    ops_spin_rpm: float | None
    # Onboard result packet
    onboard_verdict: str | None
    onboard_impact_source: str | None
    onboard_impact_timestamp_us: int | None
    onboard_club_points: int | None
    onboard_ball_points: int | None
    onboard_smash: float | None
    onboard_ball_speed_mph: float | None
    onboard_ball_speed_confidence: float | None
    onboard_vertical_launch_deg: float | None
    onboard_vertical_launch_confidence: float | None
    onboard_horizontal_launch_deg: float | None
    onboard_horizontal_launch_confidence: float | None
    onboard_club_speed_mph: float | None
    onboard_club_speed_confidence: float | None
    onboard_club_path_deg: float | None
    onboard_club_path_confidence: float | None
    onboard_angle_of_attack_deg: float | None
    onboard_angle_of_attack_confidence: float | None
    onboard_impact_range_m: float | None
    onboard_quality: str | None  # comma-joined quality flags
    # Host pipeline (LCMF-v1 launch angle, club path)
    host_vertical_launch_deg: float | None
    host_launch_confidence: float | None
    host_angle_coherence: float | None
    host_club_path_deg: float | None
    host_club_path_status: str | None
    host_attack_angle_deg: float | None
    host_track_residual: float | None
    # Cost
    capture_bytes: int | None
    dump_duration_s: float | None
    pipeline_ms: float | None
    # Deltas that the validation phases watch
    ball_speed_delta_mph: float | None  # onboard - OPS
    club_speed_delta_mph: float | None


def read_entries(path: str | Path) -> Iterator[dict]:
    """Every JSON object in a session JSONL file; malformed lines are skipped."""
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _speed_mph(metric: dict | None) -> float | None:
    if not metric or metric.get("value") is None or not metric.get("usable", True):
        return None
    return round(float(metric["value"]) * MPS_TO_MPH, 2)


def _angle_deg(metric: dict | None) -> float | None:
    if not metric or metric.get("value") is None or not metric.get("usable", True):
        return None
    return round(float(metric["value"]), 2)


def _confidence(metric: dict | None) -> float | None:
    if not metric:
        return None
    return round(float(metric.get("confidence", 0.0)), 3)


def _delta(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round(a - b, 2)


def _capture_format(config: dict | None) -> str | None:
    if not config:
        return None
    iwr = config.get("iwr6843") or {}
    if not iwr.get("enabled"):
        return None
    if iwr.get("full_capture"):
        return "iq16-full"
    return iwr.get("capture_format") or "iq16"


def collect_baseline(path: str | Path, *, firmware_sha: str | None = None) -> list[BaselineRow]:
    """Join one session's shot and capture entries into baseline rows, by shot number."""
    path = Path(path)
    session_name = path.stem
    shots: dict[int, dict] = {}
    captures: dict[int, dict] = {}
    capture_format = None
    for entry in read_entries(path):
        kind = entry.get("type")
        if kind == "session_start":
            capture_format = _capture_format(entry.get("config") or entry.get("hardware"))
        elif kind == "shot_detected":
            number = entry.get("shot_number")
            if number is not None:
                shots[int(number)] = entry
        elif kind == "iwr6843_capture":
            number = entry.get("shot_number")
            if number is not None:
                captures[int(number)] = entry
    rows: list[BaselineRow] = []
    for number in sorted(shots):
        shot = shots[number]
        capture = captures.get(number, {})
        onboard = shot.get("iwr6843_onboard") or {}
        metrics = onboard.get("metrics") or {}
        measurement = capture.get("measurement") or {}
        club_path = capture.get("club_path") or {}
        ops_ball = shot.get("ball_speed_mph")
        ops_club = shot.get("club_speed_mph")
        onboard_ball = _speed_mph(metrics.get("ball_speed"))
        onboard_club = _speed_mph(metrics.get("club_speed"))
        pipeline = shot.get("pipeline_ms")
        rows.append(
            BaselineRow(
                session=session_name,
                shot_number=number,
                timestamp=str(shot.get("timestamp") or shot.get("ts") or ""),
                club=shot.get("club"),
                capture_format=capture_format,
                firmware_sha=firmware_sha,
                ops_ball_speed_mph=ops_ball,
                ops_club_speed_mph=ops_club,
                ops_spin_rpm=shot.get("spin_rpm"),
                onboard_verdict=onboard.get("verdict"),
                onboard_impact_source=onboard.get("impact_source"),
                onboard_impact_timestamp_us=onboard.get("impact_timestamp_us"),
                onboard_club_points=onboard.get("club_points"),
                onboard_ball_points=onboard.get("ball_points"),
                onboard_smash=onboard.get("smash"),
                onboard_ball_speed_mph=onboard_ball,
                onboard_ball_speed_confidence=_confidence(metrics.get("ball_speed")),
                onboard_vertical_launch_deg=_angle_deg(metrics.get("vertical_launch")),
                onboard_vertical_launch_confidence=_confidence(metrics.get("vertical_launch")),
                onboard_horizontal_launch_deg=_angle_deg(metrics.get("horizontal_launch")),
                onboard_horizontal_launch_confidence=_confidence(metrics.get("horizontal_launch")),
                onboard_club_speed_mph=onboard_club,
                onboard_club_speed_confidence=_confidence(metrics.get("club_speed")),
                onboard_club_path_deg=_angle_deg(metrics.get("club_path")),
                onboard_club_path_confidence=_confidence(metrics.get("club_path")),
                onboard_angle_of_attack_deg=_angle_deg(metrics.get("angle_of_attack")),
                onboard_angle_of_attack_confidence=_confidence(metrics.get("angle_of_attack")),
                onboard_impact_range_m=_angle_deg(metrics.get("impact_range")),
                onboard_quality=",".join(onboard.get("quality") or []) or None,
                host_vertical_launch_deg=measurement.get("angle_deg"),
                host_launch_confidence=measurement.get("confidence"),
                host_angle_coherence=measurement.get("coherence"),
                host_club_path_deg=club_path.get("path_deg"),
                host_club_path_status=club_path.get("status"),
                host_attack_angle_deg=club_path.get("attack_angle_deg")
                or club_path.get("candidate_attack_angle_deg"),
                host_track_residual=club_path.get("residual") or measurement.get("residual"),
                capture_bytes=capture.get("capture_bytes"),
                dump_duration_s=capture.get("dump_duration_s"),
                pipeline_ms=(pipeline.get("total") if isinstance(pipeline, dict) else pipeline),
                ball_speed_delta_mph=_delta(onboard_ball, ops_ball),
                club_speed_delta_mph=_delta(onboard_club, ops_club),
            )
        )
    return rows


def collect_sessions(
    paths: Iterable[str | Path], *, firmware_sha: str | None = None
) -> list[BaselineRow]:
    """Baseline rows over several session files or directories of them."""
    rows: list[BaselineRow] = []
    for path in paths:
        path = Path(path)
        files = sorted(path.glob("session_*.jsonl")) if path.is_dir() else [path]
        for file in files:
            rows.extend(collect_baseline(file, firmware_sha=firmware_sha))
    return rows


def write_csv(rows: Iterable[BaselineRow], path: str | Path) -> int:
    """Write the rows as CSV with one column per field; returns the row count."""
    names = [f.name for f in fields(BaselineRow)]
    count = 0
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: getattr(row, name) for name in names})
            count += 1
    return count


def summarize(rows: list[BaselineRow]) -> dict[str, float | int | None]:
    """Counts and the two speed deltas' mean absolute error, for the report footer."""
    ball = [r.ball_speed_delta_mph for r in rows if r.ball_speed_delta_mph is not None]
    club = [r.club_speed_delta_mph for r in rows if r.club_speed_delta_mph is not None]
    verdicts = [r.onboard_verdict for r in rows if r.onboard_verdict]
    return {
        "shots": len(rows),
        "with_onboard_result": len(verdicts),
        "valid": verdicts.count("valid"),
        "partial": verdicts.count("partial"),
        "invalid": verdicts.count("invalid"),
        "ball_speed_mae_mph": round(sum(abs(d) for d in ball) / len(ball), 2) if ball else None,
        "club_speed_mae_mph": round(sum(abs(d) for d in club) / len(club), 2) if club else None,
    }


__all__ = [
    "ONBOARD_METRICS",
    "BaselineRow",
    "collect_baseline",
    "collect_sessions",
    "read_entries",
    "summarize",
    "write_csv",
]
