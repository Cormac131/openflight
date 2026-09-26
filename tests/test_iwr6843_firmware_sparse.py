"""Source checks for the l3sparse and self-trigger firmware paths.

The firmware cannot build or run here; these pin the properties the host
relies on. scripts/hardware-test/test_iwr_self_trigger.py checks them on a board.
"""

from __future__ import annotations

import re
from pathlib import Path

FIRMWARE = Path(__file__).parents[1] / "firmware" / "iwr6843" / "l3_dump.c"


def _source() -> str:
    return FIRMWARE.read_text(encoding="utf-8")


def _function(name: str) -> str:
    source = _source()
    start = source.rindex(name)  # the definition follows any forward declaration
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated {name}")


def test_sparse_request_buffer_uses_the_shared_limit():
    sparse = _function("int32_t l3_cli_sparse(")

    assert "char request[L3_SPARSE_REQUEST_MAX];" in sparse
    assert "request[768]" not in sparse


def test_oversized_request_is_an_error_before_any_slice_bytes():
    sparse = _function("int32_t l3_cli_sparse(")

    overflow = sparse.index("L3_READLINE_OVERFLOW")
    error = sparse.index('CLI_write("Error: sparse cell request longer', overflow)
    assert error < sparse.index('"ILS1"')


def test_read_line_drains_an_overlong_line_instead_of_stopping_mid_line():
    read_line = _function("static int32_t l3_readLine(")

    assert "while (used + 1U < cap" not in read_line
    assert "overflow = 1U;" in read_line
    assert "return L3_READLINE_OVERFLOW;" in read_line
    assert "L3_SPARSE_REQUEST_TIMEOUT_MS" in read_line


def test_slice_count_is_the_number_of_cells_actually_parsed():
    sparse = _function("int32_t l3_cli_sparse(")

    parsed = sparse.index("cellCount++")
    header = sparse.index('"ILS1"')
    count = sparse.index("l3_writeU16((uint16_t)cellCount);")
    assert parsed < header < count


def test_self_trigger_reads_a_finished_slot_beside_capture():
    """Detection runs after the slot is stored, not inside the HWA rearm task."""
    rearm = _function("static void l3_hwaRearmTask")
    detect = _function("static void l3_detectTask")
    done = _function("static void l3_hwaOutputDoneCB")
    packed = _function("static void l3_iq8EdmaDoneCB")
    stats = _function("static int32_t l3_cli_stats")

    assert "l3_considerSelfTrigger" not in rearm
    assert "l3_considerSelfTrigger(queuedSlot)" in detect
    assert "l3detect_slot_live" in detect
    assert "l3_publishDetectFrame" in done
    assert "l3_publishDetectFrame" in packed
    assert 'CLI_write("detect dropped=%u stale=%u\\n"' in stats


def test_trigger_peak_tracks_bin_zero_with_an_explicit_flag():
    consider = _function("static void l3_considerSelfTrigger(")

    assert "gTriggerPeakBin != 0U" not in consider
    assert consider.count("gTriggerHavePeak") >= 3
    assert "gTriggerHavePeak = 0U;" in _function("static void l3_clearTriggerMotion(")
    assert "l3_clearTriggerMotion();" in _function("static int32_t l3_cli_triggerCfg(")


def test_loop_means_are_computed_once_per_bin():
    loops = _function("static void l3_verticalPowerLoops(")

    # One pass accumulates the mean, a second applies it: two loop-index
    # loops per (tx, rx), never a mean loop nested inside the output loop.
    assert "meanLoop" not in loops
    assert len(re.findall(r"for \(loop = 0U; loop < loops; loop\+\+\)", loops)) == 3


def test_power_rows_go_out_in_one_write_per_loop():
    sparse = _function("int32_t l3_cli_sparse(")
    power = sparse[sparse.index("powerRow[loop * maxBins + bin]") : sparse.index("lineStatus =")]

    assert "l3_writeF32" not in power
    assert "UART_writePolling(gDataUart, (uint8_t *)&powerRow[loop * maxBins]" in power
