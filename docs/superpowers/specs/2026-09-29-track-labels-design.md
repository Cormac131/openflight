# Track labels: viewer annotate mode, label-based tests, constants fitting

Date: 2026-09-29
Status: draft, awaiting review

## Goal

Give the IWR6843 dump viewer an annotate mode so ball and club track points can
be marked by hand on real dumps. Save those labels next to the dumps so
automated tests can verify firmware changes against them. Use the labelled
dumps to fit the firmware constants offline.

## Decisions (agreed)

| # | Decision |
|---|----------|
| 1 | Labels live in a sidecar `<dump>.labels.json` beside the dump in `tests/radar/recordings/`, indexed by `manifest.json`. |
| 2 | Labels are one point per frame where the object is visible, with firmware-seeded suggestions the user corrects. A reviewed object with zero points means "must not be tracked". |
| 3 | Constants fitting is an offline sweep that prints a report. The user edits the C defaults by hand. |
| 4 | Tests use fixed per-dump tolerances plus a committed score baseline that may only ratchet up. |

The user's labels are the ground truth. The OPS243 and reference monitors are
not used here.

## Non-goals

- A range-Doppler view in the viewer.
- Automatic edits to `firmware/iwr6843/*.c` defaults.
- A held-out train/test split (the score function is kept separate so it can be added).
- Pi-side capture changes.

## Current state (from exploration)

- Viewer: Flask app `scripts/iwr6843/dump_viewer.py`, analysis in
  `src/openflight/iwr6843/dump_viewer.py`, UI in `scripts/iwr6843/dump_viewer.html`
  (Plotly). Read-only; no write-back exists.
- Dumps: `tests/radar/recordings/*.l3dump` (7), `manifest.json` with per-file
  `tee_bin`, `dest_bin`, `post_from_frame`, `expect` ranges.
- Replay: `firmware_replay.replay_dump(raw, ReplayConfig)` returns `points`
  (club), `ball_points`, frames, impact fit. Configs are built inside
  `replay_dump`; only `ReplayConfig` fields and `BallTuning` can override
  defaults.
- No per-frame truth exists anywhere.

## Design

### 1. Label file: `src/openflight/iwr6843/labels.py`

Schema v1, `<dump>.labels.json`:

```json
{
  "version": 1,
  "dump": "shot_001.l3dump",
  "dump_sha256": "...",
  "reviewed": true,
  "ball": {"points": [{"frame": 41, "range_bin": 12.4, "doppler_mps": 55.0}]},
  "club": {"points": []},
  "tolerances": {"range_bins": 1.0, "min_coverage": 0.8, "doppler_mps": null},
  "notes": ""
}
```

- `frame` is the frame index. `range_bin` is fractional. `doppler_mps` is optional.
- Replay settings (`tee_bin`, `dest_bin`, `post_from_frame`, ...) stay in
  `manifest.json`. Labels never duplicate them.
- `dump_sha256` must match the dump. A mismatch is a hard error.
- API: `load_labels`, `save_labels` (validating, atomic write via temp file and rename),
  `labels_path_for(dump)`.
- Validation: version supported, frames are non-negative integers and unique
  per object, `range_bin` finite and non-negative, tolerances in range.

### 2. Viewer annotate mode

Server (`scripts/iwr6843/dump_viewer.py`):

- `GET /api/labels?path=` returns the sidecar, or an empty template if none exists.
- `PUT /api/labels?path=` validates and saves the sidecar.
- Both resolve the path under the served root and reject traversal and non-`.l3dump` paths.

UI (`scripts/iwr6843/dump_viewer.html`):

- An Annotate toggle with an active-object selector (ball / club).
- Click on the range-time map places a point on the clicked frame at the clicked range.
  Drag moves it; delete removes it.
- "Seed from firmware" copies the replayed points for the active object in as
  editable points.
- Existing labels are drawn on the map alongside the firmware tracks.
- Editors for tolerances and notes; a "reviewed" checkbox; Save.

### 3. Scoring: `src/openflight/iwr6843/label_scoring.py`

Pure functions over `(labels, ReplayResult)`. Per object:

- Points are matched by frame.
- `matched`: labelled points whose firmware point is within `range_bins` (and
  `doppler_mps` if set).
- `coverage = matched / labelled`.
- `mean_abs_error_bins` over matched points.
- `false_points`: firmware points on frames with no label. For a reviewed
  object with zero labelled points, any firmware point counts as false.
- A scalar `score` combining coverage, error and false points. The weights are
  constants defined in this module and covered by tests.

Used by both the tests and the sweep.

### 4. Tests

Written first (TDD):

- `tests/test_iwr6843_labels.py`: schema, validation, hash mismatch, atomic save.
- `tests/test_iwr6843_label_scoring.py`: matching, coverage, false points,
  empty-object rule, score ordering.
- Viewer API tests in `tests/test_iwr6843_dump_viewer.py`: get, put, traversal
  rejection, invalid payloads.
- `tests/test_iwr6843_labelled_replay.py`: for each `manifest.json` entry with
  a reviewed label file, replay and assert (a) coverage >= `min_coverage`,
  (b) no false points on empty objects, (c) score >= the entry in
  `tests/radar/recordings/label_baseline.json`.
- Until real labelled dumps exist, one small synthetic labelled dump exercises
  the harness in CI.
- Baseline update: `scripts/analysis/fit_constants.py --update-baseline`
  rewrites `label_baseline.json` deliberately.

### 5. Constants fitting

- `src/openflight/iwr6843/tunables.py`: a registry of tunable constants, each
  with dotted name (e.g. `ball.core.gateBins`), owning struct, bounds and step.
  Initial set is taken from the trigger, club-track, ball-track, ball-hypothesis
  and impact-fit defaults.
- `ReplayConfig.overrides: Mapping[str, float]`, applied to the ctypes cfg
  structs before init. Unknown names raise. `BallTuning` keeps working on top of it.
- `scripts/analysis/fit_constants.py` (run with `uv run`): coordinate descent
  over the registry against all reviewed labelled dumps. The report lists, per
  constant, the current value, suggested value, score change and how sharp the
  optimum is. It writes nothing to firmware.

## Risks

- Only 7 dumps are committed and none is labelled. A fit is only as good as the
  labelled set, and the report states how many dumps and points it used.
- Coordinate descent may find a local optimum. The report shows the score
  curve per constant so a flat or noisy optimum is visible.
- The viewer's default dump directory (`iwr-test-sessions/`) is absent from the
  repo. Labelling uses `--dir tests/radar/recordings` or a copied session.

## Implementation order

1. `labels.py` + tests.
2. `label_scoring.py` + tests.
3. Viewer endpoints + tests, then the UI.
4. `tunables.py` and `ReplayConfig.overrides` + tests.
5. Labelled-replay test and baseline mechanism, with the synthetic dump.
6. `fit_constants.py`.
