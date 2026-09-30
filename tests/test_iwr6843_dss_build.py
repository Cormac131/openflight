"""The DSS solve needs the C6000 toolchain the SDK install used to strip."""

from __future__ import annotations

from pathlib import Path

FIRMWARE_MAKEFILE = Path(__file__).parents[1] / "firmware" / "Makefile"
FIRMWARE_DIR = Path(__file__).parents[1] / "firmware" / "iwr6843"


def test_sdk_install_keeps_the_c6000_toolchain():
    text = FIRMWARE_MAKEFILE.read_text(encoding="utf-8")
    disabled = [
        line for line in text.splitlines() if "--disable-components" in line
    ]
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
