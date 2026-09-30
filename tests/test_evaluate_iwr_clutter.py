"""Tests for scripts/analysis/evaluate_iwr_clutter.py."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from openflight.iwr6843 import firmware_replay as fr

SCRIPT = Path(__file__).parents[1] / "scripts" / "analysis" / "evaluate_iwr_clutter.py"
LABELLED = "iwr6843_20260824_120746_889_007.l3dump"


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("evaluate_iwr_clutter", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def two_recordings(tmp_path_factory):
    """Two committed recordings (one hand-labelled) with the manifest."""
    root = tmp_path_factory.mktemp("rec")
    for name in (LABELLED, "iwr6843_20260824_121024_299_013.l3dump"):
        shutil.copy(fr.RECORDINGS_DIR / name, root / name)
        shutil.copy(fr.RECORDINGS_DIR / f"{name}.labels.json", root / f"{name}.labels.json")
    shutil.copy(fr.RECORDINGS_DIR / "manifest.json", root / "manifest.json")
    return root


def test_weight_grid_is_normalised_and_complete(ev):
    grid = ev.weight_grid()
    assert len(grid) == 27
    assert all(sum(w.as_tuple()) == pytest.approx(1.0) for w in grid)


def test_bench(ev, two_recordings, tmp_path, capsys):
    out = tmp_path / "bench.json"
    assert (
        ev.main(
            ["bench", str(two_recordings), "--beta", "0.8", "--mode", "median", "--json", str(out)]
        )
        == 0
    )
    written = json.loads(out.read_text())
    assert set(written) == {"median beta=0", "median beta=0.8"}
    assert written["median beta=0.8"]["summary"]["captures"] == 2
    assert "median beta=0.8" in capsys.readouterr().out


def test_host(ev, two_recordings, capsys):
    ev.main(["host", str(two_recordings), "--beta", "0.0", "--ablations"])
    printed = capsys.readouterr().out
    assert "2 reviewed dumps" in printed
    assert "firmware" in printed and "host beta=0.0" in printed
    assert "power-dominated weights" in printed


def test_beam(ev, two_recordings, tmp_path):
    out = tmp_path / "beam.json"
    ev.main(["beam", str(two_recordings), "--json", str(out)])
    summary = json.loads(out.read_text())["summary"]
    assert summary["all"]["bartlett"]["points"] == summary["all"]["capon"]["points"] > 0


def test_rig(ev, two_recordings, tmp_path, capsys):
    manifest = tmp_path / "rig.json"
    manifest.write_text(
        json.dumps(
            {
                LABELLED: {"label": "A", "enclosure": "A"},
                "iwr6843_20260824_121024_299_013.l3dump": {"label": "B", "enclosure": "B"},
            }
        )
    )
    ev.main(["rig", str(two_recordings), "--rig-manifest", str(manifest)])
    printed = capsys.readouterr().out
    assert "| A | 1 |" in printed and "| B | 1 |" in printed
    assert "enclosure:" in printed


def test_radome(ev, tmp_path, capsys):
    out = tmp_path / "radome.json"
    ev.main(
        ["radome", "--material", "PLA", "PC", "--golfer-azimuth-deg", "-35", "--json", str(out)]
    )
    written = json.loads(out.read_text())
    assert set(written["materials"]) == {"PLA", "PC"}
    assert written["golfer_hood"]["side"] == "left"
    assert "golfer-side (left) hood depth" in capsys.readouterr().out
