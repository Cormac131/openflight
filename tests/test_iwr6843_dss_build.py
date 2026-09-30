"""The DSS solve needs the C6000 toolchain the SDK install used to strip."""

from __future__ import annotations

import re
from pathlib import Path

FIRMWARE_MAKEFILE = Path(__file__).parents[1] / "firmware" / "Makefile"
FIRMWARE_DIR = Path(__file__).parents[1] / "firmware" / "iwr6843"


def test_sdk_install_keeps_the_c6000_toolchain():
    text = FIRMWARE_MAKEFILE.read_text(encoding="utf-8")
    disabled = [line for line in text.splitlines() if "--disable-components" in line]
    assert disabled, "expected an SDK install line with --disable-components"
    for line in disabled:
        assert "TI_CGT_C6000" not in line
        assert "DSPLIB_C674x" not in line
        assert "MATHLIB_C674x" not in line


def test_meta_image_includes_a_dss_image():
    makefile = (FIRMWARE_DIR / "makefile").read_text(encoding="utf-8")
    assert "$(DSS_OUT)" in makefile
    assert (FIRMWARE_DIR / "dss" / "dss_main.c").exists()
    assert (FIRMWARE_DIR / "dss" / "dss_linker.cmd").exists()


def test_l2_cache_reservation_is_enforced_by_the_linker_not_just_the_symbol():
    """ti_sysbios_family_c64p_Cache_l2Size only programs a runtime cache-size
    register; it does not shrink the linker's L2SRAM_UMAP0/UMAP1 MEMORY
    regions (confirmed by testing: TI's linker rejects an app-cmd-file
    redefinition of a region the platform file already declared, errors
    #10263/#10264). Without something else enforcing the boundary,
    .solveScratch could silently grow into the address range the cache
    controller claims at boot, corrupting solve intermediates with no
    build-time signal.

    .cacheReserve is that something else: a real, zero-content output
    section pinned to the exact 32 KB the cache controller carves off the
    HIGH end of L2 (0x00818000-0x00820000, the top of L2SRAM_UMAP0). Because
    it is a genuine section rather than a MEMORY redefinition, the linker's
    allocator treats that range as occupied, so any other L2 section
    growing into it now fails the link with error #10099 ("program will not
    fit into available memory") instead of overlapping the cache silently.
    Verified directly: temporarily oversizing .solveScratch past the
    remaining L2 budget reproduced that #10099 error; reverting restored a
    clean, byte-identical-to-baseline DSS build.
    """
    cmd = (FIRMWARE_DIR / "dss" / "dss_linker.cmd").read_text(encoding="utf-8")
    assert "ti_sysbios_family_c64p_Cache_l2Size" in cmd

    assert ".cacheReserve" in cmd
    assert "0x00818000" in cmd, "cache reservation must sit at the top of L2SRAM_UMAP0"
    assert "0x00008000" in cmd, "cache reservation must be the full 32 KB the symbol claims"

    # The comment must explain the gap and name the enforcement mechanism,
    # so nobody reintroduces it by trusting the symbol alone.
    assert "RUNTIME register" in cmd or "runtime register" in cmd.lower()
    assert "#10099" in cmd

    # A MEMORY block that tries to redeclare an existing platform region is
    # exactly what this fix proved does NOT work -- it must not sneak back
    # in as the "real" enforcement mechanism.
    assert "MEMORY" not in cmd or "MEMORY directive cannot be edited" in cmd


# --- the MSS <-> DSS detect link (Phase 0: ping and probe) --------------------
#
# The detect task is moving to the DSS. Phase 0 proves the link on the board:
# dspPing, and dspProbe, which scores the same ring frame on both cores with
# the same code (l3_bin_score.c) and compares. The C is unit tested on the
# host (test_iwr6843_firmware_dsp.py); these pin the board glue around it.

DSS_MAIN = FIRMWARE_DIR / "dss" / "dss_main.c"
MSS_MAIN = FIRMWARE_DIR / "l3_dump.c"


def _sources(makefile: Path) -> list[str]:
    for line in makefile.read_text(encoding="utf-8").splitlines():
        if line.startswith("SOURCES"):
            return line.split("=", 1)[1].split()
    raise AssertionError(f"no SOURCES in {makefile}")


def test_both_cores_build_the_shared_scoring_and_the_link():
    for makefile in (FIRMWARE_DIR / "makefile", FIRMWARE_DIR / "dss" / "makefile"):
        sources = _sources(makefile)
        for name in ("l3_bin_score.c", "l3_dsp_ipc.c", "l3_iq16_stats.c"):
            assert name in sources, f"{name} missing from {makefile}"


def test_the_dss_links_the_mailbox_driver():
    makefile = (FIRMWARE_DIR / "dss" / "makefile").read_text(encoding="utf-8")
    libs = [line for line in makefile.splitlines() if line.startswith("DSS_STD_LIBS")]
    assert libs and "-llibmailbox_$(MMWAVE_SDK_DEVICE_TYPE)" in libs[0]


def test_the_dss_answers_the_link_over_the_mailbox():
    text = DSS_MAIN.read_text(encoding="utf-8")
    assert "Mailbox_init(MAILBOX_TYPE_DSS)" in text
    assert "Mailbox_open(MAILBOX_TYPE_MSS" in text
    assert "l3_dsp_serve(" in text, "PING, PROBE and SCORE, through the host-tested dispatcher"
    assert "Mailbox_readFlush(" in text, "a read message must be released or the next never lands"
    assert "SOC_XWR68XX_DSS_L3RAM_BASE_ADDRESS" in text, "the DSS reads L3 at its own address"


def test_the_dss_invalidates_its_cache_over_the_frame_before_scoring():
    """L2 caches L3 on the DSS and the EDMA rewrites ring slots behind it:
    without an invalidate the DSS scores a stale copy of an old frame."""
    text = DSS_MAIN.read_text(encoding="utf-8")
    invalidate = text.find("Cache_inv(")
    assert invalidate >= 0
    assert invalidate < text.find("l3_dsp_serve(")


def test_the_mss_opens_the_link_with_a_bounded_wait():
    """A DSS that never answers must fail the command, not hang the CLI."""
    text = MSS_MAIN.read_text(encoding="utf-8")
    assert "Mailbox_open(MAILBOX_TYPE_DSS" in text
    assert "L3_DSP_REPLY_TIMEOUT_TICKS" in text
    assert "readTimeout = L3_DSP_REPLY_TIMEOUT_TICKS" in text


def test_the_link_commands_are_a_trackcfg_sub_mode_not_new_table_entries():
    """The CLI table is at the SDK's CLI_MAX_CMD (32) with the mmWave
    extension's commands: a new entry would overwrite one (it did: ball)."""
    text = MSS_MAIN.read_text(encoding="utf-8")
    assert 'strcmp(argv[1], "dsp") == 0' in text
    assert "return l3_cli_trackCfgDsp(argc, argv);" in text
    assert '"dspPing"' not in text and '"dspProbe"' not in text
    entries = [int(n) for n in re.findall(r"tableEntry\[(\d+)\]\.cmd\s*=", text)]
    assert sorted(entries) == list(range(19)), "one table entry per command, 0..18"


def test_the_mss_sends_frames_as_l3_offsets_and_scores_them_itself_too():
    text = MSS_MAIN.read_text(encoding="utf-8")
    assert "SOC_XWR68XX_MSS_L3RAM_BASE_ADDRESS" in text
    assert "l3_dsp_probe_run(" in text, "the MSS runs the same probe to compare"


def test_the_dss_leaves_the_system_clock_to_the_mss():
    """SOC_SysClock_INIT on the DSS re-ungates and unhalts the BSS and spins
    on the APLL calibration flag, the MSS's job; TI's own DSS (the mmw demo)
    uses BYPASS_INIT. Suspect for the DSS not answering on the board."""
    text = DSS_MAIN.read_text(encoding="utf-8")
    assert "socCfg.clockCfg = SOC_SysClock_BYPASS_INIT;" in text
    assert "SOC_SysClock_INIT;" not in text


def test_the_dss_records_every_boot_stage_and_writes_it_back():
    """The status must leave the DSS's cache or the MSS never sees it."""
    text = DSS_MAIN.read_text(encoding="utf-8")
    for stage in (
        "L3_DSP_STAGE_MAIN",
        "L3_DSP_STAGE_SOC",
        "L3_DSP_STAGE_TASK",
        "L3_DSP_STAGE_MAILBOX",
        "L3_DSP_STAGE_LINK",
    ):
        assert f"dss_status({stage}" in text, stage
    assert "SOC_XWR68XX_DSS_HSRAM_BASE_ADDRESS + L3_DSP_STATUS_HSRAM_OFFSET" in text
    status = text[text.index("static void dss_status(") :]
    assert "Cache_wb(" in status[: status.index("\n}\n")]
    assert status.index("dss_status(L3_DSP_STAGE_MAIN") > 0
    main = text[text.index("int main(void)") :]
    assert main.index("dss_status(L3_DSP_STAGE_MAIN") < main.index("SOC_init(")


def test_the_dss_beats_while_it_waits_for_the_mss():
    """A bounded read, counted on each timeout: beats rising says BIOS runs."""
    text = DSS_MAIN.read_text(encoding="utf-8")
    assert "cfg.readTimeout = BIOS_WAIT_FOREVER;" not in text
    assert "dss_statusCount(&gDssStatus->heartbeat)" in text


def test_the_mss_prints_the_dss_status_on_request_and_when_it_does_not_answer():
    text = MSS_MAIN.read_text(encoding="utf-8")
    assert "SOC_XWR68XX_MSS_HSRAM_BASE_ADDRESS + L3_DSP_STATUS_HSRAM_OFFSET" in text
    assert 'strcmp(argv[2], "status") == 0' in text
    assert text.count("l3_dspPrintStatus();") >= 3, "status, and after each unanswered command"


# --- SCORE: the live detector's bins on the DSS ------------------------------
#
# The protocol is host tested (test_iwr6843_firmware_dsp_score.py), the
# per-frame choice (test_iwr6843_firmware_detect_core.py) and the timing
# (test_iwr6843_firmware_timing.py) too. These pin the board glue: cache
# coherence, the shared link, where the result lives, and that every
# scan-plan read goes through the one routed path.


def _function(text: str, name: str) -> str:
    start = text.rindex(name)  # the definition follows any forward declaration
    brace = text.index("{", start)
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unterminated {name}")


def test_the_mss_builds_the_detect_core_and_the_timing():
    sources = _sources(FIRMWARE_DIR / "makefile")
    for name in ("l3_detect_core.c", "l3_timing.c"):
        assert name in sources, name


def test_the_dss_answer_times_the_invalidate_apart_from_the_scoring():
    answer = _function(DSS_MAIN.read_text(encoding="utf-8"), "static void dss_answer(")
    assert "L3_DSP_CMD_SCORE" in answer, "SCORE's frame is invalidated too"
    assert answer.index("Cache_inv(") < answer.index("invCycles = TSCL - start;")
    assert answer.index("invCycles = TSCL - start;") < answer.index("l3_dsp_serve(")
    assert "gDssResult->invCycles = invCycles;" in answer
    assert "gDssResult->scoreCycles = reply->cycles;" in answer


def test_the_dss_writes_the_result_back_before_it_replies():
    """The reply is the MSS's signal to read HS-RAM: the result must have
    left the DSS's cache by then, cycles included."""
    text = DSS_MAIN.read_text(encoding="utf-8")
    answer = _function(text, "static void dss_answer(")
    assert "Cache_wb((Ptr)gDssResult, sizeof(*gDssResult)" in answer
    assert answer.index("gDssResult->scoreCycles") < answer.index("Cache_wb((Ptr)gDssResult")
    task = _function(text, "static void dss_solveTask(")
    assert task.index("dss_answer(") < task.index("Mailbox_write(")


def test_both_cores_address_the_result_block_at_their_own_hs_ram_base():
    assert "SOC_XWR68XX_DSS_HSRAM_BASE_ADDRESS + L3_DSP_RESULT_HSRAM_OFFSET" in DSS_MAIN.read_text(
        encoding="utf-8"
    )
    assert (
        "SOC_XWR68XX_MSS_HSRAM_BASE_ADDRESS +\n                                              L3_DSP_RESULT_HSRAM_OFFSET"
        in (MSS_MAIN.read_text(encoding="utf-8"))
    )


def test_the_detect_task_waits_two_frames_for_the_dss_not_a_tenth_of_a_second():
    text = MSS_MAIN.read_text(encoding="utf-8")
    match = re.search(r"#define L3_DSP_REPLY_TIMEOUT_TICKS (\d+)U", text)
    assert match and int(match.group(1)) <= 6


def test_the_link_is_one_request_at_a_time_and_the_detect_task_never_waits_for_it():
    text = MSS_MAIN.read_text(encoding="utf-8")
    exchange = _function(text, "static int32_t l3_dspExchange(")
    assert exchange.index("l3_dspLinkLock()") < exchange.index("l3_dspSend(")
    assert "l3_dspLinkUnlock();" in exchange
    try_lock = _function(text, "static int32_t l3_dspLinkTryLock(")
    assert "BIOS_NO_WAIT" in try_lock
    spans = _function(text, "static void l3_scoreSpans(")
    assert "l3_dspLinkTryLock()" in spans and "l3_dspLinkLock()" not in spans
    assert spans.count("l3_dspLinkUnlock();") == 2, "released on both routes"


def test_the_mss_accepts_only_the_answer_to_its_request_from_a_snapshot():
    score = _function(MSS_MAIN.read_text(encoding="utf-8"), "static void l3_dspScoreSpans(")
    assert "memcpy(&result, (const void *)l3_dspResultBlock(), sizeof(result));" in score
    assert score.index("memcpy(&result") < score.index("l3_dsp_result_check(")
    assert score.index("l3_dsp_result_check(") < score.index("l3_dsp_result_merge(")


def test_verify_sends_first_so_the_cores_score_at_once():
    score = _function(MSS_MAIN.read_text(encoding="utf-8"), "static void l3_dspScoreSpans(")
    send = score.index("sent = l3_dspSend(&request);")
    mss = score.index("l3_mssScoreSpans(frame, local, n, obs, scored);")
    wait = score.index("l3_dspAwait(&request, &reply)")
    assert send < mss < wait
    assert "l3_dsp_result_compare(" in score and "l3_detect_core_note_mismatch(" in score


def test_a_failed_dss_frame_falls_back_to_the_mss_and_is_reported():
    score = _function(MSS_MAIN.read_text(encoding="utf-8"), "static void l3_dspScoreSpans(")
    assert "gDetectEvent.core = (uint8_t)L3_TIMING_CORE_FALLBACK;" in score
    assert "l3_detect_core_report(" in score
    assert "L3_PROF_DSP_WAIT" in score


def test_the_latch_notice_is_not_an_error_line():
    """The host fails any command whose reply carries "Error"; a notice can
    land inside one."""
    score = _function(MSS_MAIN.read_text(encoding="utf-8"), "static void l3_dspScoreSpans(")
    notice = re.search(r'l3_queueNotice\("([^"]*)"\)', score)
    assert notice and "Error" not in notice.group(1)


def test_every_scan_plan_read_goes_through_the_routed_scoring():
    text = MSS_MAIN.read_text(encoding="utf-8")
    assert "static void l3_scoreSpan(" not in text, "the per-span MSS-only path is gone"
    trigger = _function(text, "static void l3_considerSelfTrigger(")
    assert trigger.count("l3_scoreSpans(") == 1, "one call, one DSS round trip a frame"
    assert "l3_verticalResidual(" not in trigger
    ball = _function(text, "static void l3_considerBallTrack(")
    assert ball.count("l3_scoreSpans(") == 2  # the scan plan, and the whole window without a band
    assert "l3_verticalResidual(&frame, bin" not in ball


def test_the_routed_scoring_uses_the_shared_span_scorer():
    text = MSS_MAIN.read_text(encoding="utf-8")
    assert "l3_dsp_spans_localize(" in _function(text, "static void l3_scoreSpans(")
    assert "l3_dsp_spans_score(" in _function(text, "static void l3_mssScoreSpans(")


def test_only_an_iq16_ring_frame_in_l3_goes_to_the_dss():
    eligible = _function(
        MSS_MAIN.read_text(encoding="utf-8"), "static uint8_t l3_dspFrameEligible("
    )
    for condition in (
        "frame->scratch == L3_SCRATCH_NONE",
        "frame->cb == 2U",
        "gCapturePlan.loops <= L3_IQ16_MAX_LOOPS",
        "base >= SOC_XWR68XX_MSS_L3RAM_BASE_ADDRESS",
    ):
        assert condition in eligible, condition


def test_a_read_slot_is_checked_again_after_it_was_read():
    text = MSS_MAIN.read_text(encoding="utf-8")
    stale = _function(text, "static int32_t l3_detectFrameStale(")
    assert "l3detect_slot_live(gDetectRingEpoch, gPreFramesCaptured" in stale
    assert "gDetectStaleAfterRead++;" in stale
    task = _function(text, "static void l3_detectTask(")
    assert task.index("gDetectRingEpoch = epoch;") < task.index("l3_considerSelfTrigger(")
    finish = _function(text, "static void l3_detectFinish(")
    assert "gDetectRingEpoch = L3_DETECT_POST_EPOCH;" in finish


def test_the_writer_stamps_each_frame_it_publishes():
    publish = _function(MSS_MAIN.read_text(encoding="utf-8"), "static void l3_publishDetectFrame(")
    assert "Cycleprofiler_getTimeStamp()" in publish


def test_the_detect_task_records_every_frame_it_finishes():
    task = _function(MSS_MAIN.read_text(encoding="utf-8"), "static void l3_detectTask(")
    assert task.count("l3_detectFinish();") == 2, "post frames and pre frames"
    assert "l3detect_depth(&gDetectQueue)" in task


def test_detect_core_is_a_trackcfg_sub_mode():
    text = MSS_MAIN.read_text(encoding="utf-8")
    assert 'strcmp(argv[1], "detectCore") == 0' in text
    assert "return l3_cli_trackCfgDetectCore(argc, argv);" in text
    handler = _function(text, "static int32_t l3_cli_trackCfgDetectCore(")
    assert "l3_detect_core_reset_counts(&gDetectCore);" in handler
    assert "l3_timingRestart();" in handler


def test_timing_restarts_with_each_session():
    text = MSS_MAIN.read_text(encoding="utf-8")
    session = text[text.index("/* A new session starts untriggered") - 400 :]
    assert "l3_timingRestart();" in session[:600]


def test_trigger_log_prints_the_detect_timing():
    log = _function(MSS_MAIN.read_text(encoding="utf-8"), "static int32_t l3_cli_triggerLog(")
    assert 'strcmp(argv[1], "timing") == 0' in log
    assert log.count("l3_writeDetectTiming(") == 2, "perf and timing"
