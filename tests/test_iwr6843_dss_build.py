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
    assert "l3_dsp_probe_run(" in text
    assert "Mailbox_readFlush(" in text, "a read message must be released or the next never lands"
    assert "SOC_XWR68XX_DSS_L3RAM_BASE_ADDRESS" in text, "the DSS reads L3 at its own address"


def test_the_dss_invalidates_its_cache_over_the_frame_before_scoring():
    """L2 caches L3 on the DSS and the EDMA rewrites ring slots behind it:
    without an invalidate the DSS scores a stale copy of an old frame."""
    text = DSS_MAIN.read_text(encoding="utf-8")
    invalidate = text.find("Cache_inv(")
    assert invalidate >= 0
    assert invalidate < text.find("l3_dsp_probe_run(")


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


def test_the_dss_marks_reset_before_c_init_and_bios():
    """xdc Reset functions run before cinit: a DSS stuck at 'reset' died in
    its C or BIOS startup, before main."""
    cfg = (FIRMWARE_DIR / "dss" / "dss.cfg").read_text(encoding="utf-8")
    assert "xdc.useModule('xdc.runtime.Reset')" in cfg
    assert "'&dss_resetHook'" in cfg
    text = DSS_MAIN.read_text(encoding="utf-8")
    hook = text[text.index("void dss_resetHook(void)") :]
    hook = hook[: hook.index("\n}\n")]
    assert "L3_DSP_STAGE_RESET" in hook
    assert "Cache_" not in hook, "BIOS is not up yet: no Cache calls in the reset hook"


def test_the_dss_mirrors_its_stage_into_dssgpreg0():
    text = DSS_MAIN.read_text(encoding="utf-8")
    assert "SOC_XWR68XX_DSS_DSSREG_BASE_ADDRESS" in text
    assert "L3_DSP_GPREG_TAG | stage" in text


def test_the_mss_reads_the_dss_hardware_state_without_the_dss():
    text = MSS_MAIN.read_text(encoding="utf-8")
    assert "SOC_XWR68XX_MSS_DSSREG_BASE_ADDRESS" in text
    assert "GEMPWRSMCFG4" in text and "GEMPWRSMCFG3" in text
    assert "SOC_XWR68XX_MSS_ESM_BASE_ADDRESS" in text
    assert 'strcmp(argv[2], "hw") == 0' in text
    assert text.count("l3_dspPrintHw();") >= 3, "hw, and after each unanswered command"


def test_the_dss_brackets_its_module_startup_and_hooks_exceptions():
    cfg = (FIRMWARE_DIR / "dss" / "dss.cfg").read_text(encoding="utf-8")
    assert "Startup.firstFxns.$add('&dss_startupFirst')" in cfg
    assert "Startup.lastFxns.$add('&dss_startupLast')" in cfg
    assert "xdc.useModule('ti.sysbios.family.c64p.Exception')" in cfg
    assert "Exception.exceptionHook = '&dss_exceptionHook'" in cfg
    text = DSS_MAIN.read_text(encoding="utf-8")
    for fxn, stage in (
        ("dss_startupFirst", "L3_DSP_STAGE_FIRST"),
        ("dss_startupLast", "L3_DSP_STAGE_LAST"),
        ("dss_exceptionHook", "L3_DSP_STAGE_EXCEPTION"),
    ):
        body = text[text.index(f"void {fxn}(void)\n{{") :]
        body = body[: body.index("\n}\n")]
        assert stage in body, fxn
    hook = text[text.index("void dss_exceptionHook(void)\n{") :]
    assert "Exception_getLastStatus(" in hook[: hook.index("\n}\n")]
