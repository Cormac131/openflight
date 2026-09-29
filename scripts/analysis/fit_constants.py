#!/usr/bin/env python3
"""Fit firmware constants against hand-labelled IWR6843 dumps, or refresh the test baseline.

    uv run python scripts/analysis/fit_constants.py [--dir DIR] [--passes N] [--only PREFIX]
    uv run python scripts/analysis/fit_constants.py --update-baseline

The sweep prints a report and changes nothing. ``--update-baseline`` rewrites
``label_baseline.json`` with the scores at the firmware's current defaults:
run it deliberately, after a firmware change you accept.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from openflight.iwr6843 import (
    constants_fit as cf,
    firmware_replay as fr,
    label_scoring as ls,
    tunables as tn,
)


def main(argv: list[str] | None = None) -> int:
    """Run the sweep or refresh the baseline; return the exit status."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--dir", type=Path, default=fr.RECORDINGS_DIR, help="recordings folder")
    parser.add_argument("--passes", type=int, default=2)
    parser.add_argument("--only", default="", help="only constants whose name starts with this")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)
    if args.passes < 1:
        parser.error("--passes must be at least 1")

    reviewed = ls.reviewed_recordings(args.dir)
    if not reviewed:
        raise SystemExit(f"no reviewed labels under {args.dir}: label dumps in the viewer first")

    if args.update_baseline:
        scores = {}
        for path, config, labels in reviewed:
            result = fr.replay_dump(path.read_bytes(), config)
            scores[path.name] = ls.dump_score(ls.score_labels(labels, result))
        ls.write_baseline(args.dir, scores)
        for name, score in scores.items():
            print(f"{name}: {score:.4f}")
        print(f"wrote {args.dir / ls.BASELINE_NAME}")
        return 0

    recordings = [(path.read_bytes(), config, labels) for path, config, labels in reviewed]
    try:
        baseline_score = cf.evaluate_recordings(recordings, {}, strict=True)
    except ValueError as exc:
        raise SystemExit(f"the firmware defaults do not replay these recordings: {exc}") from exc
    defaults = tn.read_defaults(fr._default_library())  # pylint: disable=protected-access
    tunables = [t for t in tn.TUNABLES if t.name.startswith(args.only)]
    if not tunables:
        raise SystemExit(f"no constant starts with {args.only!r}")

    def evaluate(overrides):
        return cf.evaluate_recordings(recordings, overrides)

    rows = cf.coordinate_descent(evaluate, tunables, defaults, passes=args.passes)
    final = {r.name: r.suggested for r in rows if r.changed}
    print(
        cf.format_report(
            rows,
            baseline_score=baseline_score,
            final_score=evaluate(final),
            n_dumps=len(recordings),
            n_points=cf.count_points(recordings),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
