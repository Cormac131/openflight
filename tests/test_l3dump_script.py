"""Host ``l3dump`` snapshot: path, completeness, and the capture loop."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843.dump import pack_dump

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "iwr6843" / "l3dump.py"
spec = importlib.util.spec_from_file_location("l3dump_script", SCRIPT)
l3dump = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = l3dump
spec.loader.exec_module(l3dump)

WHEN = datetime(2026, 9, 30, 0, 5, 1, 123456)


def _raw() -> bytes:
    return pack_dump(np.ones((2, 6, 4, 7), dtype=complex), n_tx=3, version=3)


def test_output_path_names_a_dump_like_the_session_monitor():
    path = l3dump.output_path(Path("captures"), WHEN, 2)

    assert path == Path("captures") / "iwr6843_20260930_000501_123_002.l3dump"


def test_output_path_uses_a_file_target_as_given():
    assert l3dump.output_path(Path("miss.l3dump"), WHEN, 1) == Path("miss.l3dump")


def test_output_path_defaults_to_the_current_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    assert l3dump.output_path(None, WHEN, 1) == tmp_path / "iwr6843_20260930_000501_123_001.l3dump"


def test_validated_dump_rejects_a_short_transfer():
    raw = _raw()

    with pytest.raises(ValueError, match="short IWR6843 dump"):
        l3dump.validated_dump(raw[: len(raw) // 2])


def test_save_dump_writes_a_complete_capture(tmp_path):
    raw = _raw()
    path = tmp_path / "shot.l3dump"

    metadata = l3dump.save_dump(raw, path)

    assert path.read_bytes() == raw
    assert metadata["n_frames"] == 2


def test_capture_waits_for_enter_then_writes_each_dump(tmp_path):
    raw = _raw()
    prompts: list[str] = []

    class Radar:
        def read_dump(self) -> bytes:
            return raw

    written = l3dump.capture(
        Radar(),
        count=2,
        settle_s=5.0,
        wait=True,
        out=tmp_path,
        clock=lambda: WHEN,
        pause=lambda _seconds: pytest.fail("a waited dump must not sleep"),
        prompt=prompts.append,
    )

    assert prompts == [
        "dump 1/2: press Enter to freeze the ring ",
        "dump 2/2: press Enter to freeze the ring ",
    ]
    assert [path.name for path in written] == [
        "iwr6843_20260930_000501_123_001.l3dump",
        "iwr6843_20260930_000501_123_002.l3dump",
    ]
    assert written[0].read_bytes() == raw


def test_main_rejects_one_file_for_several_dumps():
    with pytest.raises(SystemExit, match="--count 1"):
        l3dump.main(["--out", "miss.l3dump", "--count", "2"])


def test_port_name_error_rejects_a_windows_name_on_the_pi():
    assert l3dump.port_name_error("COM5", "linux")
    assert l3dump.port_name_error("COM5", "win32") is None
