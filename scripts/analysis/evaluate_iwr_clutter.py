#!/usr/bin/env python3
"""Golfer-clutter robustness: benchmark every step on the same recorded dumps.

    uv run python scripts/analysis/evaluate_iwr_clutter.py bench [DIR ...] \\
        [--beta 0.5 0.6 0.7 0.8 0.9 1.0] [--mode median ema] [--json out.json]
    uv run python scripts/analysis/evaluate_iwr_clutter.py host [DIR ...] \\
        [--beta 0.0 0.8] [--sweep-weights] [--ablations]
    uv run python scripts/analysis/evaluate_iwr_clutter.py beam [DIR ...] [--loading 0.1]
    uv run python scripts/analysis/evaluate_iwr_clutter.py rig DIR --rig-manifest rig.json
    uv run python scripts/analysis/evaluate_iwr_clutter.py radome [--material PLA]

``bench`` replays the firmware with the clutter map at each beta and mode
(beta 0 learns but subtracts nothing: the reference) and prints the
benchmark summary (``clutter_bench``) and the Phase 1 acceptance lines.
``host`` runs the host kinematic tracker (``association``) and scores its
club and ball against the reviewed hand labels beside the firmware's;
``--sweep-weights`` searches the evidence weights, ``--ablations`` turns
the clutter probability, the field of view and the kinematic weighting off
one at a time. ``beam`` compares Bartlett and Capon (``beamforming``) at the
firmware club track's points against the golfer's learned direction and,
with ``--tracking``, by the host tracker's label scores with each one's angles.
``rig`` groups the benchmark by the rig manifest's conditions (orientation
and enclosure, ``rig_experiments``). ``radome`` prints window thicknesses
and losses (``radome``).

Directories default to the committed recordings; each needs a
``manifest.json`` (``firmware_replay.recording_entry``) or ``--tee-bin``.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from openflight.iwr6843 import (
    association as asc,
    beamforming as bf,
    clutter_bench as cb,
    firmware_replay as fr,
    radome,
    rig_experiments,
)
from openflight.iwr6843.clutter_map import ClutterConfig, RangeAngleClutterMap
from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.label_scoring import score_object
from openflight.iwr6843.labels import load_labels
from openflight.iwr6843.tracking import RANGE_SPAN_M

BETAS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
WEIGHT_GRID = {
    "range": (0.2, 0.3, 0.4),
    "velocity": (0.15, 0.25, 0.35),
    "power": (0.05, 0.2, 0.5),
}


def cases(roots: Sequence[Path], tee_bin: int | None) -> list[tuple[Path, fr.ReplayConfig]]:
    """Every range-snapshot dump under the roots with its replay configuration."""
    found = []
    for root in roots:
        for path in sorted(Path(root).rglob("*.l3dump")):
            entry = fr.recording_entry(path, default_tee_bin=tee_bin)
            found.append((path, fr.ReplayConfig(**entry)))
    return found


def _rounded(summary: dict) -> dict:
    return {k: round(v, 3) if isinstance(v, float) else v for k, v in summary.items()}


def run_bench(args: argparse.Namespace) -> dict:
    """Phase 0/1: the benchmark at every beta and mode, against beta 0."""
    todo = cases(args.roots, args.tee_bin)
    results: dict[str, dict] = {}
    for mode in args.mode:
        reference = [
            cb.benchmark_file(p, replace(c, clutter=ClutterConfig(beta=0.0, mode=mode)))
            for p, c in todo
        ]
        results[f"{mode} beta=0"] = {"summary": cb.summarize(reference), "acceptance": []}
        for beta in args.beta:
            config = ClutterConfig(beta=beta, mode=mode, history=args.history, alpha=args.alpha)
            metrics = [cb.benchmark_file(p, replace(c, clutter=config)) for p, c in todo]
            results[f"{mode} beta={beta}"] = {
                "summary": cb.summarize(metrics),
                "acceptance": cb.acceptance(metrics, reference),
                "captures": [asdict(m) for m in metrics],
            }
    for label, entry in results.items():
        print(label, json.dumps(_rounded(entry["summary"])))
        for line in entry["acceptance"]:
            print("   FAIL", line)
        if label.endswith("beta=0"):
            continue
        if not entry["acceptance"]:
            print("   acceptance: pass")
    return results


def _geometry(config: fr.ReplayConfig) -> asc.SceneGeometry:
    return asc.SceneGeometry(tee_range_m=config.destination * RANGE_SPAN_M / config.fft_size)


def host_scores(todo, *, beta: float, tracker: asc.TrackerConfig | None = None) -> dict:
    """Mean label coverage and score of the host tracker's club and ball."""
    club_cov, ball_cov, club_score, ball_score = [], [], [], []
    false_points = 0
    for path, config, labels in todo:
        result = asc.track_dump(
            path.read_bytes(),
            _geometry(config),
            config=tracker,
            clutter=ClutterConfig(beta=beta),
            stat=config.stat,
            impact_frame=config.post_from_frame,
            fft_size=config.fft_size,
        )
        club = score_object(labels.club, result.club_points, labels.tolerances)
        ball = score_object(labels.ball, result.ball_points, labels.tolerances)
        if club.labelled:
            club_cov.append(club.coverage)
            club_score.append(club.score)
        if ball.labelled:
            ball_cov.append(ball.coverage)
            ball_score.append(ball.score)
        false_points += club.false_points + ball.false_points
    mean = lambda v: statistics.fmean(v) if v else None  # noqa: E731
    return {
        "club_coverage": mean(club_cov),
        "ball_coverage": mean(ball_cov),
        "club_score": mean(club_score),
        "ball_score": mean(ball_score),
        "false_points": false_points,
    }


def firmware_scores(todo) -> dict:
    """The same label numbers for the firmware replay, for comparison."""
    club_cov, ball_cov, club_score, ball_score = [], [], [], []
    for path, config, labels in todo:
        result = fr.replay_dump(path.read_bytes(), config)
        club = score_object(labels.club, result.points, labels.tolerances)
        ball = score_object(labels.ball, result.ball_points, labels.tolerances)
        if club.labelled:
            club_cov.append(club.coverage)
            club_score.append(club.score)
        if ball.labelled:
            ball_cov.append(ball.coverage)
            ball_score.append(ball.score)
    return {
        "club_coverage": statistics.fmean(club_cov),
        "ball_coverage": statistics.fmean(ball_cov),
        "club_score": statistics.fmean(club_score),
        "ball_score": statistics.fmean(ball_score),
    }


def weight_grid() -> list[asc.ScoreWeights]:
    """The sweep: range, velocity and power over WEIGHT_GRID, the rest in the
    default proportions, every set normalised."""
    default = asc.ScoreWeights()
    rest = default.acceleration + default.angle + default.history
    out = []
    for r, v, p in itertools.product(*WEIGHT_GRID.values()):
        remaining = max(1.0 - r - v - p, 0.0)
        scale = remaining / rest
        out.append(
            asc.ScoreWeights(
                r,
                v,
                default.acceleration * scale,
                default.angle * scale,
                default.history * scale,
                p,
            ).normalised()
        )
    return out


def run_host(args: argparse.Namespace) -> dict:
    """Phases 2-5: the host tracker against the labels, beside the firmware."""
    todo = [
        (p, c, labels)
        for p, c in cases(args.roots, args.tee_bin)
        if (labels := load_labels(p)) is not None and labels.reviewed
    ]
    out = {"labelled_dumps": len(todo), "firmware": firmware_scores(todo)}
    print(f"{len(todo)} reviewed dumps")
    print("firmware", json.dumps(_rounded(out["firmware"])))
    for beta in args.beta:
        out[f"host beta={beta}"] = host_scores(todo, beta=beta)
        print(f"host beta={beta}", json.dumps(_rounded(out[f"host beta={beta}"])))
    if args.ablations:
        beta = args.beta[-1]
        ablations = {
            "power-dominated weights": asc.TrackerConfig(
                weights=asc.ScoreWeights(0.1, 0.05, 0.0, 0.0, 0.05, 0.8)
            ),
            "no clutter probability": asc.TrackerConfig(clutter_weight=0.0),
            "no field of view": asc.TrackerConfig(fov_floor=1.0),
        }
        for name, tracker in ablations.items():
            out[name] = host_scores(todo, beta=beta, tracker=tracker)
            print(f"{name} (beta={beta})", json.dumps(_rounded(out[name])))
    if args.sweep_weights:
        beta = args.beta[-1]
        rows = []
        for weights in weight_grid():
            scores = host_scores(todo, beta=beta, tracker=asc.TrackerConfig(weights=weights))
            rows.append((weights, scores))
        rows.sort(key=lambda row: -(row[1]["club_score"] + row[1]["ball_score"]))
        out["sweep"] = [{"weights": asdict(w), **s} for w, s in rows]
        for weights, scores in rows[:5]:
            print("sweep", json.dumps(_rounded(asdict(weights))), json.dumps(_rounded(scores)))
    return out


def _golfer_direction(cube, meta, split: int, hotspot: int, n_tx: int) -> float | None:
    config = ClutterConfig(beta=0.0, min_updates=1)
    ra = RangeAngleClutterMap(config, bf.GRID_RAD, 128)
    for frame in range(min(split, int(meta["n_frames"]))):
        start, count = fr.frame_window(meta, frame)
        if not start <= hotspot < start + count:
            continue
        local = hotspot - start
        residual = bf.residual_at(cube, frame, local, n_tx)
        chirp = bf.chirp_phase_rad(bf.lag1_phase_rad(residual), n_tx, 0.0)
        elements, _ = bf.elevation_snapshots(residual, chirp)
        ra.update(hotspot, bf.bartlett_spectrum(elements)[None, :])
    found = ra.dominant_direction(hotspot, 1)
    return None if found is None else found[0]


def run_beam(args: argparse.Namespace) -> dict:
    """Phase 6: Bartlett vs Capon at the firmware club points before impact,
    the golfer's direction learned at the hotspot bin."""
    rows = []
    for path, config in cases(args.roots, args.tee_bin):
        raw = path.read_bytes()
        result = fr.replay_dump(raw, config)
        metrics = cb.capture_metrics(path.name, raw, result)
        split = cb.impact_frame(result)
        if metrics.hotspot_bin is None or split is None:
            continue
        meta, cube = parse_dump(raw)
        n_tx = int(meta["n_tx"])
        interferer = _golfer_direction(cube, meta, split, metrics.hotspot_bin, n_tx)
        if interferer is None:
            continue
        club = [p for p in result.points if p.frame < split]
        for older, point in zip(club, club[1:], strict=False):
            dt = (point.timestamp_us - older.timestamp_us) * 1e-6
            start, count = fr.frame_window(meta, point.frame)
            local = int(round(point.range_bin)) - start
            if dt <= 0.0 or not 0 <= local < count:
                continue
            residual = bf.residual_at(cube, point.frame, local, n_tx)
            speed = (point.range_m - older.range_m) / dt
            chirp = bf.chirp_phase_rad(bf.lag1_phase_rad(residual), n_tx, speed)
            elements, _ = bf.elevation_snapshots(residual, chirp)
            in_hotspot = abs(point.range_bin - metrics.hotspot_bin) <= cb.HOTSPOT_HALF_BINS
            for c in bf.compare_beamformers(elements, interferer, loading=args.loading):
                rows.append(
                    {
                        "capture": path.name,
                        "frame": point.frame,
                        "in_hotspot": in_hotspot,
                        "method": c.method,
                        "rejection_db": c.rejection_db,
                        "cost": c.cost.total,
                    }
                )
    summary = {
        "all": beam_summary(rows),
        "in_hotspot": beam_summary([r for r in rows if r["in_hotspot"]]),
    }
    for subset, entry in summary.items():
        print(
            subset,
            json.dumps(_rounded({k: v for k, v in entry.items() if not isinstance(v, dict)})),
        )
        for method in ("bartlett", "capon"):
            print("  ", method, json.dumps(_rounded(entry[method])))
    if args.tracking:
        todo = [
            (p, c, labels)
            for p, c in cases(args.roots, args.tee_bin)
            if (labels := load_labels(p)) is not None and labels.reviewed
        ]
        summary["tracking"] = {
            name: host_scores(todo, beta=0.0, tracker=asc.TrackerConfig(beamformer=name))
            for name in ("bartlett", "capon")
        }
        for name, scores in summary["tracking"].items():
            print("tracking", name, json.dumps(_rounded(scores)))
    return {"summary": summary, "rows": rows}


def beam_summary(rows: Sequence[dict]) -> dict:
    """Median rejection per method and the paired Capon-minus-Bartlett gain;
    ``rows`` alternate bartlett, capon for the same snapshots."""
    summary: dict = {}
    for method in ("bartlett", "capon"):
        values = [
            r["rejection_db"]
            for r in rows
            if r["method"] == method and r["rejection_db"] is not None
        ]
        costs = [r["cost"] for r in rows if r["method"] == method]
        summary[method] = {
            "points": len(values),
            "median_rejection_db": statistics.median(values) if values else None,
            "cost_macs": costs[0] if costs else None,
        }
    paired = [
        c["rejection_db"] - b["rejection_db"]
        for b, c in zip(rows[0::2], rows[1::2], strict=True)
        if b["rejection_db"] is not None and c["rejection_db"] is not None
    ]
    summary["capon_minus_bartlett_db"] = statistics.median(paired) if paired else None
    summary["capon_better_fraction"] = (
        sum(1 for d in paired if d > 0) / len(paired) if paired else None
    )
    return summary


def run_rig(args: argparse.Namespace) -> dict:
    """Phases 7 and 8: the benchmark grouped by rig condition."""
    conditions = rig_experiments.load_rig_manifest(args.rig_manifest)
    metrics = [
        cb.benchmark_file(p, c) for p, c in cases(args.roots, args.tee_bin) if p.name in conditions
    ]
    rows = rig_experiments.rig_matrix(metrics, conditions)
    print(rig_experiments.format_matrix(rows))
    best = rig_experiments.best_orientation([r for r in rows if r.enclosure is None])
    if best is not None:
        print(f"best orientation: {best.label} (combined SCR {best.combined_scr_db:.1f} dB)")
    verdict = rig_experiments.enclosure_verdict(rows)
    print(f"enclosure: {verdict}")
    return {"rows": [asdict(r) for r in rows], "enclosure": verdict}


def run_radome(args: argparse.Namespace) -> dict:
    """Phases 9-11: window thickness, loss, standoff and hood geometry."""
    names = args.material or list(radome.MATERIALS)
    rows = {}
    print("| Material | eps_r | tan d | n lambda_m/2 (mm) | best (mm) | worst 2-way loss (dB) |")
    print("|---|---|---|---|---|---|")
    for name in names:
        material = radome.MATERIALS[name]
        halves = radome.half_wave_thicknesses_mm(material.eps_r, count=3)
        best, loss = radome.best_thickness_mm(material, max_angle_deg=args.fov_deg)
        rows[name] = {"half_waves_mm": halves, "best_mm": best, "worst_loss_db": loss}
        print(
            f"| {name} | {material.eps_r} | {material.loss_tangent} | "
            f"{', '.join(f'{h:.2f}' for h in halves)} | {best:.2f} | {loss:.2f} |"
        )
    standoffs = radome.standoff_candidates_mm()
    print("standoff candidates (mm):", ", ".join(f"{s:.2f}" for s in standoffs))
    out = {"materials": rows, "standoff_candidates_mm": standoffs}
    if args.golfer_azimuth_deg is not None:
        cutoff = max(abs(args.golfer_azimuth_deg) - args.golfer_margin_deg, 1.0)
        depth = radome.hood_depth_for_cutoff_mm(args.hood_half_opening_mm, cutoff)
        side = "right" if args.golfer_azimuth_deg > 0 else "left"
        print(f"golfer-side ({side}) hood depth to shade beyond {cutoff:.1f} deg: {depth:.1f} mm")
        out["golfer_hood"] = {"side": side, "cutoff_deg": cutoff, "depth_mm": depth}
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def with_roots(p: argparse.ArgumentParser, *, required: bool = False) -> None:
        p.add_argument(
            "roots",
            nargs="+" if required else "*",
            type=Path,
            default=[] if required else [fr.RECORDINGS_DIR],
            help="Folders searched for .l3dump files (default: the committed recordings)",
        )
        p.add_argument("--tee-bin", type=int, help="Tee bin for dumps without a manifest entry")
        p.add_argument("--json", type=Path, help="Write the full results here")

    bench = sub.add_parser("bench", help="Phase 0/1: the clutter map beta sweep")
    with_roots(bench)
    bench.add_argument("--beta", type=float, nargs="+", default=list(BETAS))
    bench.add_argument("--mode", nargs="+", choices=("median", "ema"), default=["median", "ema"])
    bench.add_argument("--history", type=int, default=ClutterConfig().history)
    bench.add_argument("--alpha", type=float, default=ClutterConfig().alpha)
    host = sub.add_parser("host", help="Phases 2-5: the host tracker against the labels")
    with_roots(host)
    host.add_argument("--beta", type=float, nargs="+", default=[0.0, 0.8])
    host.add_argument("--sweep-weights", action="store_true")
    host.add_argument("--ablations", action="store_true")
    beam = sub.add_parser("beam", help="Phase 6: Bartlett vs Capon")
    with_roots(beam)
    beam.add_argument("--loading", type=float, default=bf.DEFAULT_LOADING)
    beam.add_argument(
        "--tracking",
        action="store_true",
        help="Also score the host tracker with each beamformer's angles against the labels",
    )
    rig = sub.add_parser("rig", help="Phases 7/8: orientation and enclosure matrix")
    with_roots(rig, required=True)
    rig.add_argument("--rig-manifest", type=Path, required=True)
    rad = sub.add_parser("radome", help="Phases 9-11: window and hood geometry")
    rad.add_argument("--material", nargs="+", choices=sorted(radome.MATERIALS))
    rad.add_argument("--fov-deg", type=float, default=30.0)
    rad.add_argument("--golfer-azimuth-deg", type=float)
    rad.add_argument("--golfer-margin-deg", type=float, default=10.0)
    rad.add_argument("--hood-half-opening-mm", type=float, default=20.0)
    rad.add_argument("--json", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runner = {
        "bench": run_bench,
        "host": run_host,
        "beam": run_beam,
        "rig": run_rig,
        "radome": run_radome,
    }[args.command]
    result = runner(args)
    if getattr(args, "json", None) is not None:
        args.json.write_text(
            json.dumps(result, indent=2, default=_json_default) + "\n", encoding="utf-8"
        )
    return 0


def _json_default(value):
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


if __name__ == "__main__":
    raise SystemExit(main())
