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
