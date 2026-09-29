"""The firmware's tracks against the hand labels committed beside the recordings.

Every ``*.l3dump`` in ``tests/radar/recordings`` that has a reviewed
``<dump>.labels.json`` is replayed with its manifest configuration. The
firmware must cover the labelled frames within the file's tolerances, track
nothing on an object labelled empty, and not score below the committed
baseline. To accept a deliberate change run
``uv run python scripts/analysis/fit_constants.py --update-baseline``.
"""

from __future__ import annotations

import pytest

from openflight.iwr6843 import firmware_host as fw, firmware_replay as fr, label_scoring as ls

needs_compiler = pytest.mark.skipif(
    fw.host_compiler() is None, reason="no C compiler for the firmware modules"
)

_RECORDINGS = ls.reviewed_recordings(fr.RECORDINGS_DIR) if fr.RECORDINGS_DIR.exists() else []
_BASELINE = ls.load_baseline(fr.RECORDINGS_DIR) if fr.RECORDINGS_DIR.exists() else {}


@needs_compiler
@pytest.mark.parametrize(
    ("path", "config", "labels"), _RECORDINGS, ids=[r[0].name for r in _RECORDINGS]
)
def test_firmware_tracks_match_the_labels(path, config, labels):
    result = fr.replay_dump(path.read_bytes(), config)
    scores = ls.score_labels(labels, result)
    assert ls.check_labels(labels, scores) == [], f"{path.name}: {scores}"
    assert path.name in _BASELINE, (
        f"{path.name} has no baseline score; run "
        "`uv run python scripts/analysis/fit_constants.py --update-baseline`"
    )
    score = ls.dump_score(scores)
    assert score >= _BASELINE[path.name] - 1e-9, (
        f"{path.name}: score {score:.4f} fell below the baseline {_BASELINE[path.name]:.4f}; "
        f"{scores}"
    )
