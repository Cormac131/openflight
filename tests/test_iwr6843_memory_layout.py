"""The IQ16 scratch must live outside L3 so capture owns the whole arena."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
FIRMWARE = ROOT / "firmware" / "iwr6843" / "l3_dump.c"
MAP = ROOT / "firmware" / "iwr6843" / "l3_dump_mss.map"
BASELINE_MAP = ROOT / "firmware" / "iwr6843" / "baseline" / "l3_dump_mss.map.baseline"
MIN_DATA_RAM_FREE_BYTES = 16 * 1024


def _function_source(source: str, name: str, next_name: str) -> str:
    start = source.rindex(name)
    end = source.index(next_name, start)
    return source[start:end]


def test_scratch_is_not_carved_out_of_the_capture_arena():
    """L3_IQ8_CAPTURE_BYTES was deleted outright (its only value was always
    L3_TOTAL_BYTES once the scratch left L3), so the equivalent surviving
    construct is l3_captureCapacityBytes() returning L3_TOTAL_BYTES
    unconditionally, with no offset-cast into g_ring and no IQ8/IQ16 branch
    left over from the old capacity split."""
    source = FIRMWARE.read_text(encoding="utf-8")
    assert "L3_IQ8_CAPTURE_BYTES" not in source
    assert "&g_ring[L3_IQ8_CAPTURE_BYTES]" not in source

    capacity = _function_source(
        source,
        "static uint32_t l3_captureCapacityBytes",
        "static uint32_t l3_captureBytesPerComplex",
    )
    assert "return L3_TOTAL_BYTES;" in capacity
    assert "l3_captureUsesIq8" not in capacity
    assert "L3_RING_IQ8" not in capacity


def test_scratch_is_a_real_array_in_its_own_section():
    source = FIRMWARE.read_text(encoding="utf-8")
    assert 'DATA_SECTION(g_iq16FrameScratch, ".dataScratch")' in source
    assert "static int16_t g_iq16FrameScratch[2][L3_IQ16_SCRATCH_WORDS];" in source


def _memory_rows(path: Path = MAP) -> dict[str, tuple[int, int]]:
    if not path.exists():
        pytest.skip(
            f"no linker map at {path}; this check needs a local firmware build "
            f"(see the plan's Global Constraints for the docker command). The "
            f"_Static_assert in l3_dump.c enforces the scratch size at build "
            f"time regardless, and test_baseline_map_geometry_is_intact below "
            f"always runs."
        )
    rows: dict[str, tuple[int, int]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(
            r"\s+(\w+)\s+([0-9a-f]{8})\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)",
            line,
        )
        if match:
            rows.setdefault(match.group(1), (int(match.group(4), 16), int(match.group(5), 16)))
    if not rows:
        pytest.fail(f"could not parse MEMORY CONFIGURATION from {MAP}")
    return rows


def test_data_ram_keeps_a_working_margin():
    _used, unused = _memory_rows()["DATA_RAM"]
    assert unused >= MIN_DATA_RAM_FREE_BYTES, (
        f"DATA_RAM free margin fell to {unused} B, below {MIN_DATA_RAM_FREE_BYTES} B"
    )


def test_l3_is_fully_claimed_by_the_capture_ring():
    used, unused = _memory_rows()["L3_RAM"]
    assert unused == 0
    assert used == 786_432


def test_baseline_map_geometry_is_intact():
    """Always runs: the baseline map IS tracked, unlike the build output.

    Guards the parser itself and catches a corrupted or truncated baseline,
    so CI keeps real coverage even with no toolchain present.
    """
    rows = _memory_rows(BASELINE_MAP)
    assert rows["L3_RAM"] == (786_432, 0)
    baseline_data_ram_free = rows["DATA_RAM"][1]
    assert baseline_data_ram_free >= 98_304 + MIN_DATA_RAM_FREE_BYTES, (
        "the pre-relocation baseline no longer has room for a 98,304 B scratch "
        "plus the required margin; the relocation premise is broken"
    )
