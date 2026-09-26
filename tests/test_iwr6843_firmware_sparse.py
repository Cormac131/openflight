"""Source checks for the l3sparse and self-trigger firmware paths.

l3_dump.c cannot build here (it needs the mmWave SDK); these pin the
properties the host relies on. The detector itself is built and exercised
in test_iwr6843_firmware_trigger.py. scripts/hardware-test/test_iwr_self_trigger.py checks them on a board.
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


def test_trigger_scores_every_loop_not_just_loop_zero():
    """The detector's observation integrates the MTI residual over all loops."""
    source = _source()
    consider = _function("static void l3_considerSelfTrigger(")

    assert "l3_verticalPowerAt" not in source
    assert "perLoop[0]" not in source
    assert "l3_verticalResidual(slot, first + bin, NULL, &obs[bin]);" in consider
    assert "l3_trig_update(&gTrig, gPreFramesCaptured, first, obs, count)" in consider


def test_trigger_no_longer_gates_on_the_tee_bin_or_a_toward_away_sequence():
    consider = _function("static void l3_considerSelfTrigger(")

    for retired in ("gTriggerPower", "gTriggerToward", "gTriggerAway", "gTriggerReady"):
        assert retired not in _source(), retired
    assert "l3_trig_region(&gTrigCfg" in consider


def test_trigger_freeze_request_is_unchanged_by_the_new_detector():
    consider = _function("static void l3_considerSelfTrigger(")
    freeze = consider[consider.index("key = Hwi_disable();") : consider.index("Hwi_restore(key);")]

    for line in (
        "gHwaFreezeRequested = 1U;",
        "gPostCaptureStarted = 0U;",
        "gPostFramesCaptured = 0U;",
        "gPostFramesObserved = 0U;",
        "gActiveFrameShouldKeep = 1U;",
        "gSelfTriggerLatched = 1U;",
        "gHwaFreezeRequests++;",
    ):
        assert line in freeze, line
    assert 'CLI_write("Triggered\\n");' in consider


def test_trigger_config_waits_for_a_frame_in_progress_before_resetting():
    cfg = _function("static int32_t l3_cli_triggerCfg(")

    assert (
        cfg.index("gTriggerEnabled = 0U;")
        < cfg.index("while (gTrigBusy && waited")
        < cfg.index("l3_trig_init(&gTrig")
    )
    consider = _function("static void l3_considerSelfTrigger(")
    assert (
        consider.index("gTrigBusy = 1U;")
        < consider.index("l3_trig_update(")
        < consider.index("gTrigBusy = 0U;")
    )


def test_trigger_config_disables_on_zero_frames_and_checks_the_rest():
    cfg = _function("static int32_t l3_cli_triggerCfg(")

    assert "if (cfg.trackFrames != 0U && l3_trig_cfg_check(&cfg) != 0)" in cfg
    assert "gTriggerEnabled = (cfg.trackFrames != 0U) ? 1U : 0U;" in cfg
    assert "argc < 4 || argc > 9" in cfg


def test_every_ring_rearm_resets_the_detector_but_keeps_its_log():
    source = _source()

    assert source.count("    gPreFramesCaptured = 0U;\n    l3_trigRearm();\n") == 3
    assert "l3_trig_rearm(&gTrig);" in _function("static void l3_trigRearm(")


def test_trigger_log_command_is_registered_and_ends_with_done():
    source = _source()
    log = _function("static int32_t l3_cli_triggerLog(")

    assert 'cliCfg.tableEntry[13].cmd           = "triggerLog";' in source
    assert "l3_trig_format_summary(&gTrig" in log
    assert "l3_trig_format_config(&gTrig" in log
    assert log.rindex('CLI_write("Done\\n");') > log.rindex("l3_trig_format_record(")


def test_detector_source_is_built_into_the_firmware():
    makefile = (FIRMWARE.parent / "makefile").read_text(encoding="utf-8")

    assert "SOURCES    = l3_dump.c l3_trigger.c" in makefile
    assert '#include "l3_trigger.h"' in _source()


def test_loop_period_for_doppler_comes_from_the_accepted_profile():
    source = _source()

    assert "gTrigLoopPeriodS = (float)(profCfg.idleTimeConst + profCfg.rampEndTime)" in source
    assert "gTrig.loopPeriodS = gTrigLoopPeriodS;" in _function(
        "static void l3_considerSelfTrigger("
    )


def test_loop_means_are_computed_once_per_bin():
    residual = _function("static void l3_verticalResidual(")

    # One pass accumulates the mean, a second applies it: two loop-index
    # loops per (tx, rx), never a mean loop nested inside the output loop.
    # The other two are the per-loop init and the final peak/copy pass.
    assert "meanLoop" not in residual
    assert len(re.findall(r"for \(loop = 0U; loop < loops; loop\+\+\)", residual)) == 4
    # The sparse rows and the trigger share that one pass.
    assert "l3_verticalResidual(slot, localBin, out, NULL);" in _function(
        "static void l3_verticalPowerLoops("
    )


def test_residual_walks_loops_by_stride_instead_of_recomputing_indices():
    residual = _function("static void l3_verticalResidual(")

    assert "l3_iq16Sample" not in residual
    assert "uint32_t loopStride = ntx * N_RX * binCount * 2U;" in residual
    assert residual.count("sample += loopStride;") == 2
    # Energy, strongest loop and the Doppler autocorrelation come from the
    # same pass; no second walk over the samples.
    for field in ("obs->energy = energy;", "obs->peak = peak;", "obs->r1Re = r1Re;"):
        assert field in residual, field


def test_power_rows_go_out_in_one_write_per_loop():
    sparse = _function("int32_t l3_cli_sparse(")
    power = sparse[sparse.index("powerRow[loop * maxBins + bin]") : sparse.index("lineStatus =")]

    assert "l3_writeF32" not in power
    assert "UART_writePolling(gDataUart, (uint8_t *)&powerRow[loop * maxBins]" in power
