"""Source checks for the l3sparse and self-trigger firmware paths.

l3_dump.c cannot build here (it needs the mmWave SDK); these pin the
properties the host relies on. The detector itself is built and exercised
in test_iwr6843_firmware_trigger.py. scripts/hardware-test/test_iwr_firmware.py
runs the readback section against a board.
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
    assert "while (drained < L3_READLINE_DRAIN_MAX)" in read_line
    assert "status = L3_READLINE_OVERFLOW;" in read_line
    assert "L3_SPARSE_REQUEST_TIMEOUT_MS" in read_line


def test_latched_self_trigger_stops_the_front_end_before_rearm():
    """HWA freeze leaves the BSS chirping. MMWave_start then returns
    'RF restart failed' unless this wait stops the front end first."""
    wait = _function("static int32_t l3_awaitFrozenRing(")
    latched = wait.index("if (gSelfTriggerLatched)")
    timeout_return = wait.index("return -1;", wait.index("self-trigger freeze timed out", latched))
    stop = wait.index("return l3_finishCaptureStop();", timeout_return)
    boundary = wait.index("l3_stopCaptureAtBoundary()", stop)

    assert timeout_return < stop < boundary


def test_release_rearms_without_reading_a_cell_line():
    """A second CLI line cannot sit in the one-byte SCI receiver during the power dump."""
    release = _function("static int32_t l3_cli_release(")

    assert "l3_readLine" not in release
    assert "l3_awaitFrozenRing()" in release
    assert release.index("l3_awaitFrozenRing()") < release.index("l3_sparseRearm()")
    assert 'tableEntry[16].cmd           = "l3release"' in _source()


def test_blank_line_before_the_cell_request_is_not_a_missing_request():
    """A stray CR/LF left in the FIFO must not reject the real cells line."""
    sparse = _function("int32_t l3_cli_sparse(")
    read_line = _function("static int32_t l3_readLine(")

    assert "L3_READLINE_EMPTY" in read_line
    retry = sparse.index("lineStatus == L3_READLINE_EMPTY")
    missing = sparse.index('CLI_write("Error: sparse cell request missing')
    assert retry < missing
    assert sparse.count("l3_readLine(request") == 2


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
    consider = _function("static void l3_considerSelfTrigger(")

    assert "l3_considerSelfTrigger" not in rearm
    assert "l3_considerSelfTrigger(queuedSlot)" in detect
    assert "l3detect_slot_live" in detect
    assert "l3_publishDetectFrame" in done
    assert "l3_publishDetectFrame" in packed
    assert 'CLI_write("detect dropped=%u stale=%u notice_dropped=%u\\n"' in stats
    assert "gPreFramesCaptured < gCapturePlan.preFrames" in consider


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
    assert 'l3_queueNotice("Triggered\\n");' in consider


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
    assert "argc < 4 || argc > 10" in cfg


def test_every_ring_rearm_resets_the_detector_but_keeps_its_log():
    source = _source()

    assert source.count("    gPreFramesCaptured = 0U;\n    l3_trigRearm();\n") == 3
    assert "l3_trig_rearm(&gTrig);" in _function("static void l3_trigRearm(")


def test_trigger_log_command_is_registered_and_ends_with_done():
    source = _source()
    log = _function("static int32_t l3_cli_triggerLog(")

    assert 'cliCfg.tableEntry[17].cmd           = "triggerLog";' in source
    assert "l3_trig_format_summary(&gTrig" in log
    assert "l3_trig_format_config(&gTrig" in log
    assert log.rindex('CLI_write("Done\\n");') > log.rindex("l3_trig_format_record(")


def test_detect_task_never_writes_the_cli_uart_itself():
    """A host command mid-line would let the CLI task splice its reply into the notice."""
    source = _source()
    consider = _function("static void l3_considerSelfTrigger(")
    note = _function("static void l3_noteTrigger(")

    assert "CLI_write" not in consider
    assert "CLI_write" not in note
    assert 'l3_queueNotice("Triggered\\n");' in consider
    assert consider.index('l3_queueNotice("Triggered') < consider.index(
        "l3_noteTrigger(9U, gTrig.floor)"
    )
    assert "l3_queueNotice(line);" in note
    assert "#define L3_NOTICE_TASK_PRIORITY L3_CLI_TASK_PRIORITY" in source
    assert "Task_create(l3_noticeTask, &taskParams, NULL);" in source


def test_debug_cfg_answers_with_the_current_line_before_done():
    handler = _function("static int32_t l3_cli_debugCfg(")

    assert handler.index("l3_formatTriggerDebug(gTriggerPhase") < handler.index('CLI_write("Done')
    assert "l3_queueNotice" not in handler


def test_notice_queue_writes_whole_lines_from_one_task():
    task = _function("static void l3_noticeTask(")
    queue = _function("static void l3_queueNotice(")

    assert 'CLI_write("%s", gNoticeLines[gNoticeHead]);' in task
    assert "gNoticeDropped++;" in queue
    assert "Hwi_disable()" in queue and "Semaphore_post(gNoticeSemaphore);" in queue


def test_overlong_request_drain_outlasts_a_four_times_oversized_request():
    source = _source()
    read_line = _function("static int32_t l3_readLine(")

    assert "#define L3_READLINE_DRAIN_MAX (64U * L3_SPARSE_REQUEST_MAX)" in source
    assert "while (drained < L3_READLINE_DRAIN_MAX)" in read_line
    assert "4U * cap" not in read_line


def test_trigger_log_serves_the_raw_input_trace_and_its_clear():
    """A missed swing must be readable: trace and clear ride the existing command."""
    source = _source()
    log = _function("static int32_t l3_cli_triggerLog(")
    trace = _function("static void l3_writeTriggerTrace(")

    assert 'strcmp(argv[1], "trace") == 0' in log and "l3_writeTriggerTrace(line" in log
    assert 'strcmp(argv[1], "clear") == 0' in log and "l3_trig_trace_clear(&gTrig);" in log
    assert "l3_trig_format_trace_header(&gTrig" in trace
    assert "l3_trig_format_maxhold(&gTrig, index, 8U" in trace
    assert "l3_trig_format_trace(&entry" in trace
    assert "tableEntry[19]" not in source, "the CLI table is at the SDK's command limit"
    assert "obs->loop0 = loopPower[0];" in _function("static void l3_verticalResidual(")


def test_sensor_stop_takes_a_self_trigger_freeze_instead_of_closing_over_it():
    """A fire nobody read back left the BSS chirping; closing then wedged the CLI."""
    stop = _function("static int32_t l3_cli_sensorStop(")

    assert stop.index("if (gSelfTriggerLatched) {") < stop.index("else if (gCaptureActive)")
    assert "status = l3_awaitFrozenRing();" in stop
    assert stop.index("l3_awaitFrozenRing") < stop.index("MMWave_close")


def test_doppler_speed_gate_is_optional_and_rides_the_same_pass():
    cfg = _function("static int32_t l3_cli_triggerCfg(")
    assert "cfg.minSpeedMps = strtof(argv[9], &end);" in cfg


def test_tee_scan_reports_static_power_the_trigger_never_sees():
    """A stationary ball is exactly what MTI removes; teeScan reads it back raw."""
    source = _source()
    static = _function("static float l3_verticalStaticPower(")
    scan = _function("static int32_t l3_cli_teeScan(")

    assert "meanIm" not in static and "meanRe" not in static, "no mean subtraction: static power"
    assert "total += im * im + re * re;" in static
    assert "return (samples > 0U) ? (total / (float)samples) : 0.0F;" in static
    assert (
        scan.index("l3_sparseFreeze()")
        < scan.index("l3_verticalStaticPower(")
        < scan.index("return l3_sparseRearm();")
    )
    assert "if (window.slots[frame] >= gCapturePlan.preFrames)" in scan, "pre frames only"
    assert 'CLI_write("teescan frames=%u loops=%u first=%u count=%u start=%u\\n"' in scan
    assert 'CLI_write("bin=%u power=%u\\n"' in scan
    assert 'cliCfg.tableEntry[18].cmd           = "teeScan";' in source
    assert "tableEntry[19]" not in source


def test_detector_source_is_built_into_the_firmware():
    makefile = (FIRMWARE.parent / "makefile").read_text(encoding="utf-8")

    assert "l3_trigger.c" in makefile
    assert "live_selector.c" in makefile
    assert "track_select.c" in makefile
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


def test_read_line_uses_the_buffered_uart_receive_not_register_polling():
    """The SCI receiver holds one byte. Polling SCIRD from the CLI task loses a
    byte whenever the HWA rearm task (now above the CLI) preempts the poll, and
    a 700-byte cell line spans several frames. On the Pi every l3sparse cell
    request came back "missing" or truncated. UART_read moves the byte capture
    into the driver's RX interrupt; echo must be off so that ISR does not spin
    on TX between bytes."""
    read_line = _function("static int32_t l3_readLine(")
    source = _source()

    assert "UART_read(gCliUart" in read_line
    assert "SCIRD" not in read_line
    assert "SCIFLR" not in read_line
    assert "Task_sleep" not in read_line
    assert "readTimeout = L3_SPARSE_REQUEST_TIMEOUT_MS" in read_line
    init = " ".join(source.split())  # the open block aligns its '=' with spaces
    assert "uartParams.readEcho = UART_ECHO_OFF;" in init
    assert init.index("uartParams.readEcho = UART_ECHO_OFF;") < init.index("gCliUart = UART_open(0")
