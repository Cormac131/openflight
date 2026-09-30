"""The angle estimate's steering table, firmware/iwr6843/l3_angle.c.

l3_angle_bartlett scanned 161 elevation steps and computed each step's
steering rotor with sinf and a cosf/sinf phasor on every call: 483
transcendentals on the R4F per estimate (~1.6 ms with the rest). The rotors
depend only on the grid, so they are computed once (l3_angle_tables_init,
the same sinf/cosf calls) and the scan reads them: the same values, so the
same answers. tests/fixtures/iwr6843_bartlett_golden.json holds the scan's
outputs recorded from the implementation before the table.
"""

from __future__ import annotations

import ctypes
import json
import math
import struct
from pathlib import Path

import pytest

from openflight.iwr6843 import firmware_host as fw

GOLDEN = Path(__file__).parent / "fixtures" / "iwr6843_bartlett_golden.json"
FIRMWARE = Path(__file__).parents[1] / "firmware" / "iwr6843"


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if fw.host_compiler() is None:
        pytest.skip("no C compiler for the firmware modules")
    return fw.build_firmware_library(tmp_path_factory.mktemp("l3_host"))


def _float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _cases():
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _cases(), ids=lambda case: f"n{case['n']}")
def test_the_scan_answers_as_it_did_before_the_table(lib, case):
    """Same rotors, same arithmetic: the recorded answers. The tolerance is a
    few float ulps only, for a host whose libm rounds sinf differently from
    the one that recorded them."""
    elements = (fw.Cpx * 8)()
    for m in range(case["n"]):
        elements[m].re = _float(case["re"][m])
        elements[m].im = _float(case["im"][m])
    ratio = ctypes.c_float()

    angle = lib.l3_angle_bartlett(elements, case["n"], ctypes.byref(ratio))

    assert angle == pytest.approx(_float(case["angle"]), rel=1e-6, abs=1e-7)
    assert ratio.value == pytest.approx(_float(case["ratio"]), rel=1e-5)


def test_the_table_holds_each_steps_rotor(lib):
    lib.l3_angle_tables_init()
    for step in (0, 80, 160):
        rotor = fw.Cpx()
        assert lib.l3_angle_steering_rotor(step, ctypes.byref(rotor)) == 0
        theta = (step - 80) * 0.00872665
        phase = -math.pi * math.sin(theta)
        assert rotor.re == pytest.approx(math.cos(phase), abs=2e-6)
        assert rotor.im == pytest.approx(math.sin(phase), abs=2e-6)


def test_a_step_off_the_grid_is_refused(lib):
    rotor = fw.Cpx(re=7.0, im=7.0)
    assert lib.l3_angle_steering_rotor(161, ctypes.byref(rotor)) == -1
    assert (rotor.re, rotor.im) == (7.0, 7.0)


def test_initialising_twice_changes_nothing(lib):
    first, again = fw.Cpx(), fw.Cpx()
    lib.l3_angle_tables_init()
    lib.l3_angle_steering_rotor(37, ctypes.byref(first))
    lib.l3_angle_tables_init()
    lib.l3_angle_steering_rotor(37, ctypes.byref(again))
    assert bytes(first) == bytes(again)


def test_the_scan_does_not_compute_rotors_itself():
    """The point of the table: no transcendental in the scan's loop."""
    source = (FIRMWARE / "l3_angle.c").read_text(encoding="utf-8")
    scan = source[source.index("float l3_angle_bartlett(") :]
    scan = scan[: scan.index("\n}\n")]
    assert "sinf(" not in scan and "cosf(" not in scan and "l3_angle_phasor(" not in scan
    assert "l3_angle_tables_init();" in scan, "a host caller that skipped init still works"


def test_the_board_fills_the_table_before_any_task_runs():
    source = (FIRMWARE / "l3_dump.c").read_text(encoding="utf-8")
    init = source[source.index("static void l3_initTask(UArg arg0, UArg arg1)") :]
    init = init[: init.index("Task_create(")]
    assert "l3_angle_tables_init();" in init


def test_the_table_lives_in_hs_ram_not_the_full_data_ram_nor_read_only_tcm():
    """DATA_RAM is full, and the SDK's prebuilt SOC library maps the program
    TCM read-only unless loaded from CCS: a table filled at init there
    faults on a flashed board. HS-RAM's MSS section is writable."""
    source = (FIRMWARE / "l3_angle.c").read_text(encoding="utf-8")
    assert '__attribute__((section(".hsramMss")))' in source
    assert ".progData" not in source
    cmd = (FIRMWARE / "mss_linker.cmd").read_text(encoding="utf-8")
    assert ".progData" not in cmd
