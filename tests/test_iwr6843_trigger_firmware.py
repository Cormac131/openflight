"""Run the firmware self-trigger detector natively on synthetic range power.

Compiles ``l3_considerSelfTrigger`` and ``l3_clearTriggerMotion`` straight out
of ``l3_dump.c`` with the host C compiler, with stubs for the capture globals,
so the lifecycle guards are executed rather than only grepped.
"""

from __future__ import annotations

import ctypes
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843.self_trigger import PHASES, BallLeaveDetector

FIRMWARE = Path(__file__).parents[1] / "firmware" / "iwr6843" / "l3_dump.c"

TEE_BIN = 20
LEVEL = 1000.0
BINS = 53
PHASE_NO_FRAME = 1
PHASE_BIN_OUTSIDE = 2
PHASE_FIRED = 9
FRAME_PERIOD_US = 3000


def _function(source: str, signature: str) -> str:
    start = source.rindex(signature)
    brace = source.index("{", start)
    depth = 0
    for end in range(brace, len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start : end + 1]
    raise AssertionError(signature)


def _function_body(source: str, name: str, next_name: str) -> str:
    start = source.rindex(name)
    return source[start : source.index(next_name, start)]


@pytest.fixture(scope="module")
def detector(tmp_path_factory):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("C compiler unavailable")
    source = FIRMWARE.read_text(encoding="utf-8")
    trigger_globals = "\n".join(
        line.replace("volatile ", "")
        for line in re.findall(r"^static volatile[^\n]*\bgTrigger\w*[^\n]*;", source, re.M)
    )
    approach = "\n".join(re.findall(r"^#define L3_TRIGGER_\w+[^\n]*", source, re.M))
    harness = f"""
#include <stdint.h>
#include <stddef.h>
{approach}
static uint8_t gSelfTriggerLatched, gHwaFreezeRequested, gPostCaptureStarted;
static uint32_t gPreFramesCaptured;
static uint16_t gFramePeriodUs = {FRAME_PERIOD_US}U;
static uint32_t gFrameBinCount[4];
static struct {{ uint32_t preFrames, loops, preBins; }} gCapturePlan;
static const float *gPowers;
static float l3_verticalPowerAt(uint32_t slot, uint32_t bin) {{ (void)slot; return gPowers[bin]; }}
{trigger_globals}
{_function(source, "static void l3_clearTriggerMotion(void)")}
static void l3_noteTrigger(uint8_t phase, float tee, float approach)
{{
    (void)tee; (void)approach;
    gTriggerPhase = phase;
}}
static void l3_latchSelfTrigger(float tee, float approach)
{{
    gSelfTriggerLatched = 1U;
    l3_clearTriggerMotion();
    l3_noteTrigger(9U, tee, approach);
}}
{_function(source, "static void l3_considerSelfTrigger(uint32_t slot)")}

void reset(uint32_t pre_frames, uint32_t captured, uint32_t bins)
{{
    uint32_t i;
    l3_clearTriggerMotion();
    gSelfTriggerLatched = 0U; gHwaFreezeRequested = 0U; gPostCaptureStarted = 0U;
    gTriggerEnabled = 1U; gTriggerBin = {TEE_BIN}U; gTriggerPower = {LEVEL}F; gTriggerHits = 2U;
    gCapturePlan.preFrames = pre_frames; gCapturePlan.loops = 12U; gCapturePlan.preBins = bins;
    gPreFramesCaptured = captured;
    for (i = 0U; i < 4U; i++) {{ gFrameBinCount[i] = bins; }}
}}
void set_captured(uint32_t captured) {{ gPreFramesCaptured = captured; }}
void set_period(uint32_t us) {{ gFramePeriodUs = (uint16_t)us; }}
void set_bins(uint32_t slot, uint32_t bins) {{ gFrameBinCount[slot] = bins; }}
int step(uint32_t slot, const float *powers)
{{
    gPowers = powers;
    gPreFramesCaptured++;
    l3_considerSelfTrigger(slot);
    return gTriggerPhase;
}}
int latched(void) {{ return gSelfTriggerLatched; }}
int toward(void) {{ return gTriggerToward; }}
int have_peak(void) {{ return gTriggerHavePeak; }}
"""
    build = tmp_path_factory.mktemp("trigger")
    c_file = build / "detector.c"
    c_file.write_text(harness, encoding="utf-8")
    library = build / "detector.so"
    # Every gTrigger* global comes along; the ones this unit never reads are fine.
    flags = ["-std=c99", "-Wall", "-Werror", "-Wno-unused-variable", "-shared", "-fPIC"]
    subprocess.run([compiler, *flags, str(c_file), "-o", str(library)], check=True)
    lib = ctypes.CDLL(str(library))
    lib.step.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_float)]
    lib.reset.argtypes = [ctypes.c_uint32] * 3
    lib.set_captured.argtypes = [ctypes.c_uint32]
    lib.set_bins.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    lib.set_period.argtypes = [ctypes.c_uint32]
    return lib


def _frame(**returns: float) -> np.ndarray:
    """Range power with the tee occupied plus returns at ``b<bin>=power``."""
    power = np.zeros(BINS, dtype=np.float32)
    power[TEE_BIN] = 5000.0
    for key, value in returns.items():
        power[int(key[1:])] = value
    return power


def _step(lib, frame: np.ndarray, slot: int = 0) -> int:
    return lib.step(slot, frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))


# A club approaching the tee, then energy past it moving farther out: fires
# on the sixth frame, when the departure has progressed.
_SWING = (
    _frame(),
    _frame(),
    _frame(b12=3000),
    _frame(b15=3000),
    _frame(b17=3000, b24=8000),
    _frame(b26=8000),
)


def test_a_swing_fires_once_the_history_is_full(detector):
    detector.reset(4, 4, BINS)

    phases = [_step(detector, frame) for frame in _SWING]

    assert phases[-1] == PHASE_FIRED
    assert detector.latched()


def test_no_trigger_until_the_pre_trigger_history_is_full(detector):
    """A freeze before the ring fills would hand the host unwritten pre-trigger frames."""
    detector.reset(24, 0, BINS)

    phases = [_step(detector, frame) for frame in _SWING]

    assert set(phases) == {PHASE_NO_FRAME}
    assert not detector.latched()


def test_the_detector_starts_on_the_frame_that_completes_the_history(detector):
    detector.reset(3, 0, BINS)

    phases = [_step(detector, frame) for frame in _SWING]

    assert phases[:2] == [PHASE_NO_FRAME, PHASE_NO_FRAME]
    assert PHASE_NO_FRAME not in phases[2:]


def test_a_frame_without_the_tee_bin_clears_the_approach(detector):
    """Mirrors the host replay (test_missing_tee_bin_clears_motion): a gap loses the approach."""
    detector.reset(4, 4, BINS)
    for frame in _SWING[:4]:
        _step(detector, frame)
    assert detector.toward()

    detector.set_bins(1, TEE_BIN)
    assert _step(detector, _frame(b17=3000), slot=1) == PHASE_BIN_OUTSIDE

    assert not detector.toward()
    assert _step(detector, _SWING[-1]) != PHASE_FIRED
    assert not detector.latched()


def _rows(frames: list[dict[int, float]]) -> list[np.ndarray]:
    rows = []
    for peaks in frames:
        row = np.zeros(BINS, dtype=np.float32)
        for bin_index, power in peaks.items():
            row[bin_index] = power
        rows.append(row)
    return rows


@pytest.mark.parametrize("period_us", [2000, 3000, 4000])
@pytest.mark.parametrize("case", ["reversal", "departure", "gap", "stationary", "timeout"])
def test_firmware_matches_the_host_replay_and_fires_only_on_departure(detector, period_us, case):
    """Frame by frame, the C detector and BallLeaveDetector report the same phase."""
    tee = TEE_BIN
    # Approach returns sit inside the L3_TRIGGER_APPROACH_BINS window below the tee.
    frames = [
        {tee: 1000.0},
        {tee: 1000.0},
        {tee: 1000.0, tee - 10: 1500.0},
        {tee: 1000.0, tee - 6: 1500.0},
    ]
    if case == "reversal":
        # The approach peak walks back without anything passing the tee.
        frames += [{tee: 1000.0, tee - 9: 1500.0}]
    elif case == "departure":
        # Energy past the tee moves farther out on the next frame.
        frames += [{tee + 2: 1500.0}, {tee + 4: 1500.0}]
    elif case == "gap":
        # The approach vanishes for longer than the missed-frame allowance.
        frames += [{tee: 1000.0}] * 300 + [
            {tee: 1000.0, tee - 9: 1500.0, tee + 2: 1600.0},
            {tee + 4: 1600.0},
        ]
    elif case == "stationary":
        # Something sits past the tee without moving: not a ball leaving.
        frames += [{tee + 2: 1500.0}] * 5
    else:
        # Approach motion that never resolves times out before the departure.
        frames += [{tee: 1000.0, tee - 6: 1500.0}] * 40 + [{tee + 2: 1500.0}, {tee + 4: 1500.0}]
    detector.reset(4, 4, BINS)
    detector.set_period(period_us)
    replay = BallLeaveDetector(level=LEVEL, hits=2, frame_period_s=period_us / 1e6)

    phases = []
    for index, row in enumerate(_rows(frames)):
        phase = PHASES[_step(detector, row)]
        phases.append(phase)
        assert phase == replay.step(index, row, TEE_BIN, BINS).phase, (case, index, phases)

    detector.set_period(FRAME_PERIOD_US)
    assert ("fired" in phases) == (case == "departure"), (case, phases)


def test_sensor_start_forgets_every_trigger_from_the_previous_session():
    """A latch or enable left over from a crashed host would freeze or self-trigger the new one."""
    source = FIRMWARE.read_text(encoding="utf-8")
    start = _function_body(
        source, "static int32_t l3_cli_sensorStart(int32_t argc", "static int32_t l3_cli_sensorStop"
    )

    assert "gSelfTriggerLatched = 0U;" in start
    assert "gTriggerEnabled = 0U;" in start
    assert "l3_clearTriggerMotion();" in start
    assert source.index("static void l3_clearTriggerMotion(void)\n{") < source.index(
        "static int32_t l3_cli_sensorStart(int32_t argc, char *argv[])\n{"
    )


def test_trigger_cfg_clears_motion_through_the_shared_helper():
    source = FIRMWARE.read_text(encoding="utf-8")
    trigger_cfg = _function_body(
        source, "static int32_t l3_cli_triggerCfg(int32_t argc", "static int32_t l3_cli_debugCfg"
    )

    assert "l3_clearTriggerMotion();" in trigger_cfg
    assert "gTriggerToward = 0U;" not in trigger_cfg
