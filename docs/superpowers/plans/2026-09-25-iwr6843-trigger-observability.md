# IWR6843 Trigger Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a frozen IWR6843 self-trigger diagnosable from one `stats` command, and stop the per-frame debug line from running inside the HWA rearm task.

**Architecture:** `l3_cli_stats` already returns the capture counters the host prints verbatim. Append one `trig` line to that reply, carrying phase, tee power, latched, and enabled. `l3_writeTriggerDebug` keeps the same line format and emits it only when the phase changes. The leave-rule hysteresis and the dense-profile power-loop collapse stay out of this change until the hardware gates below pass.

**Tech Stack:** IWR6843 MSS C firmware (`firmware/iwr6843/l3_dump.c`), host replay in `src/openflight/iwr6843/self_trigger.py`, source-contract tests via `uv run pytest`.

**Spec:** This plan. There is no separate spec. The approved scope is the observability change only.

## Global Constraints

- Use `uv run pytest` for Python tests. Do not call bare `pytest` or `python`.
- A new or changed behavior needs a failing test before the firmware edit.
- Append the `trig` line. Do not reorder or rename existing `stats` tokens. `IWR6843Radar.stop_sensor` checks that the reply contains `active=0`.
- Do not change the dump header, sample order, or `l3sparse` / `l3track` wire format.
- Do not add narrative comments. The phase-change guard should be obvious from the variable name.
- Do not start Task 2 or Task 3 in the same commit as Task 1.
- Firmware build, when requested, is `docker run --rm --platform linux/amd64 -v "${PWD}:/work" -w /work openflight-iwr-sdk:latest make -C firmware build-native RELEASE_NAME=l3_dump_configurable_capture_20260818.bin` from the repo root. Task 1 does not flash the board.

---

### Task 1: Report trigger state from `stats` and print debug only on phase change

**Files:**
- Modify: `firmware/iwr6843/l3_dump.c` (`l3_writeTriggerDebug` near line 2819, `l3_cli_debugCfg` near line 3377, `l3_cli_stats` return near line 3510)
- Test: `tests/test_iwr6843_firmware_rearm.py`

**Interfaces:**
- Consumes: existing `gTriggerPhase`, `gTriggerTeePower`, `gSelfTriggerLatched`, `gTriggerEnabled`, `l3_triggerPhaseName`.
- Produces: a second `stats` line, `trig phase=<name> tee=<unsigned> latched=<0|1> enabled=<0|1>`, printed before `return 0` in `l3_cli_stats`. `debugCfg 0` sets `gTriggerDebugPhase` to `0xFF` so the next `debugCfg 1` prints the current phase once.

- [ ] **Step 1: Write the failing test**

Add this test to `tests/test_iwr6843_firmware_rearm.py`:

```python
def test_stats_reports_trigger_state_and_debug_prints_on_phase_change_only():
    """A missed Triggered line must still be visible, without a per-frame UART write."""
    source = FIRMWARE.read_text(encoding="utf-8")
    stats = _function_source(source, "static int32_t l3_cli_stats", "static int32_t l3_cli_hwaStats")
    debug_write = _function_source(
        source,
        "static void l3_writeTriggerDebug",
        "static void l3_noteTrigger",
    )
    debug_cfg = _function_source(
        source,
        "static int32_t l3_cli_debugCfg",
        "static int32_t l3_cli_stats",
    )

    assert 'CLI_write("trig phase=%s tee=%u latched=%u enabled=%u\\n"' in stats
    assert "l3_triggerPhaseName(gTriggerPhase)" in stats
    assert stats.index("trig phase=") < stats.index("return 0")
    assert "if (phase == gTriggerDebugPhase)" in debug_write
    assert debug_write.index("gTriggerDebugPhase = phase") < debug_write.index("CLI_write(")
    assert "gTriggerDebugPhase = 0xFFU" in debug_cfg
```

If `l3_cli_hwaStats` is inside `#ifdef ENABLE_HWA_SMOKE` and `_function_source` cannot find it, use the next real function token after `l3_cli_stats` instead. Keep the assertions the same.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_iwr6843_firmware_rearm.py::test_stats_reports_trigger_state_and_debug_prints_on_phase_change_only -q --tb=short`

Expected: FAIL because the `trig` format string and `gTriggerDebugPhase` are absent.

- [ ] **Step 3: Write the minimal implementation**

Next to the other trigger state variables (the `gTriggerDebug` declaration), add:

```c
static volatile uint8_t  gTriggerDebugPhase = 0xFFU;
```

Replace `l3_writeTriggerDebug` with:

```c
static void l3_writeTriggerDebug(uint8_t phase)
{
    if (!gTriggerDebug || phase == gTriggerDebugPhase) {
        return;
    }
    gTriggerDebugPhase = phase;
    CLI_write(
        "trig phase=%s tee=%u approach=%u ready=%u toward=%u away=%u "
        "run=%u peak=%u have=%u bin=%u level=%u latched=%u\n",
        l3_triggerPhaseName(phase),
        (unsigned)gTriggerTeePower,
        (unsigned)gTriggerApproachPower,
        (unsigned)gTriggerReady,
        (unsigned)gTriggerToward,
        (unsigned)gTriggerAway,
        (unsigned)gTriggerRun,
        (unsigned)gTriggerPeakBin,
        (unsigned)gTriggerHavePeak,
        (unsigned)gTriggerBin,
        (unsigned)gTriggerPower,
        (unsigned)gSelfTriggerLatched);
}
```

In `l3_cli_debugCfg`, set the last-printed phase whenever debug is turned off, before `CLI_write("Done\n")` on the success path:

```c
gTriggerDebug = (uint8_t)enabled;
if (!gTriggerDebug) {
    gTriggerDebugPhase = 0xFFU;
} else {
    l3_writeTriggerDebug(gTriggerPhase);
}
```

Remove the old `if (gTriggerDebug) { l3_writeTriggerDebug(gTriggerPhase); }` that this replaces.

Immediately before `return 0;` at the end of `l3_cli_stats`, after every `#endif` of the format branches, add:

```c
CLI_write("trig phase=%s tee=%u latched=%u enabled=%u\n",
          l3_triggerPhaseName(gTriggerPhase),
          (unsigned)gTriggerTeePower,
          (unsigned)gSelfTriggerLatched,
          (unsigned)gTriggerEnabled);
```

`l3_triggerPhaseName` exists only under `CONFIGURABLE_CAPTURE`. Guard this write with `#ifdef CONFIGURABLE_CAPTURE` so the non-configurable build still compiles. Do not put the line inside only one of the IQ8 format branches.

> **Note (2026-09-26):** `CONFIGURABLE_CAPTURE` no longer exists — a later task
> (`2026-09-25-iwr6843-longer-movie`) collapsed it out of `l3_dump.c` and
> dropped the define from `firmware/Makefile`. Do not add an `#ifdef
> CONFIGURABLE_CAPTURE` guard; if this step is still relevant, write the
> `CLI_write` unconditionally instead.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_iwr6843_firmware_rearm.py tests/test_iwr6843_firmware_sparse.py tests/test_iwr6843_self_trigger.py -q --tb=line`

Expected: PASS. The self-trigger tests must stay green because this task does not change the leave rule.

- [ ] **Step 5: Commit**

```bash
git add firmware/iwr6843/l3_dump.c tests/test_iwr6843_firmware_rearm.py
git commit -m "$(cat <<'EOF'
feat(iwr6843): report self-trigger state from stats

A frozen ring was only visible if the host caught the one Triggered line.
EOF
)"
```

---

### Task 2: Hold the leave rule — do not start until a flashed swing confirms Task 1

**Files:**
- Modify: `firmware/iwr6843/l3_dump.c` (`l3_considerSelfTrigger`, about lines 2931-2958)
- Modify: `src/openflight/iwr6843/self_trigger.py` (`BallLeaveDetector.step` and `_departed`)
- Test: `tests/test_iwr6843_self_trigger.py`

**Interfaces:**
- Consumes: `gTriggerToward`, `gTriggerHavePeak`, `gTriggerPeakBin`, `peakBin`, `pastPeak`.
- Produces: two new counters, `gTriggerRetreatFrames` and `gTriggerDepartFrames`, both cleared by `l3_clearTriggerMotion`. The host mirror uses `_retreat_frames` and `_depart_frames`.

**Gate:** Stop here unless both of these are true:

1. The Task 1 image is flashed and a ball on the tee produces `phase=fired` on the swing, with `tee` still above the level.
2. The same image, ball sitting still and nobody in the beam, stays off `fired` for at least a minute (`stats` shows `latched=0`).

If either check fails, fix that before adding hysteresis. A one-bin hold will hide hits that the current rule just started catching.

- [ ] **Step 1: Write the failing tests**

Add these to `tests/test_iwr6843_self_trigger.py`. Construct `BallLeaveDetector` the same way the existing one-frame tests do.

```python
def test_one_bin_walk_back_for_one_frame_does_not_fire():
    detector = BallLeaveDetector(level=LEVEL, hits=2)
    detector.step(0, _row({TEE_BIN: LEVEL}), TEE_BIN, 53)
    detector.step(1, _row({TEE_BIN: LEVEL}), TEE_BIN, 53)
    toward = detector.step(2, _row({TEE_BIN: LEVEL, 10: LEVEL}), TEE_BIN, 53)
    retreated = detector.step(3, _row({TEE_BIN: LEVEL, 9: LEVEL}), TEE_BIN, 53)

    assert toward.phase == "toward"
    assert retreated.phase != "fired"


def test_walk_back_of_two_bins_fires_on_that_frame():
    detector = BallLeaveDetector(level=LEVEL, hits=2)
    detector.step(0, _row({TEE_BIN: LEVEL}), TEE_BIN, 53)
    detector.step(1, _row({TEE_BIN: LEVEL}), TEE_BIN, 53)
    detector.step(2, _row({TEE_BIN: LEVEL, 10: LEVEL}), TEE_BIN, 53)
    fired = detector.step(3, _row({TEE_BIN: LEVEL, 8: LEVEL}), TEE_BIN, 53)

    assert fired.fired


def test_one_frame_of_energy_past_the_tee_does_not_fire():
    detector = BallLeaveDetector(level=LEVEL, hits=2)
    detector.step(0, _row({TEE_BIN: LEVEL}), TEE_BIN, 53)
    detector.step(1, _row({TEE_BIN: LEVEL}), TEE_BIN, 53)
    detector.step(2, _row({TEE_BIN: LEVEL, 10: LEVEL}), TEE_BIN, 53)
    once = detector.step(3, _row({TEE_BIN: LEVEL, 10: LEVEL, TEE_BIN + 2: LEVEL + 1}), TEE_BIN, 53)
    twice = detector.step(4, _row({TEE_BIN: LEVEL, 10: LEVEL, TEE_BIN + 2: LEVEL + 1}), TEE_BIN, 53)

    assert once.phase != "fired"
    assert twice.fired
```

`_row`, `TEE_BIN`, and `LEVEL` are already defined in that test file.

Update `test_energy_past_the_tee_fires_while_the_tee_stays_loud` so the past-tee frame is fed twice before asserting `fired`. Update `test_toward_then_away_then_a_quiet_tee_fires` only if its walk-back is a single one-bin step; that test must still end `fired` by using a two-bin step or a second retreat frame.

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_self_trigger.py::test_one_bin_walk_back_for_one_frame_does_not_fire tests/test_iwr6843_self_trigger.py::test_one_frame_of_energy_past_the_tee_does_not_fire -q --tb=short`

Expected: FAIL because one frame of either leave signal still latches.

- [ ] **Step 3: Implement the hold in the host mirror and the firmware together**

In `BallLeaveDetector._clear`, set `_retreat_frames = 0` and `_depart_frames = 0`. Initialize both to `0` in `__init__`.

Replace the one-frame `away` assignment and the immediate latch in `step`:

```python
        if self._have_peak and peak_bin > self._peak_bin:
            self._toward = True
            self._retreat_frames = 0
        elif self._toward and self._have_peak and peak_bin + 1 < self._peak_bin:
            self._away = True
            self._retreat_frames = 0
        elif self._toward and self._have_peak and peak_bin < self._peak_bin:
            self._retreat_frames += 1
            if self._retreat_frames >= 2:
                self._away = True
        else:
            self._retreat_frames = 0
        self._peak_bin = peak_bin
        self._have_peak = True
        self._approach_power = peak
        departed = self._departed(power, tee_local, valid_bins, peak)
        if departed:
            self._depart_frames += 1
        else:
            self._depart_frames = 0
        if self._away or self._depart_frames >= 2:
            self._latch()
            return self._observe(frame, "fired", tee, peak)
```

In `l3_clearTriggerMotion`, clear `gTriggerRetreatFrames` and `gTriggerDepartFrames`. Declare both next to `gTriggerAway` as `static volatile uint32_t`, starting at `0U`.

Replace the `gTriggerAway` block in `l3_considerSelfTrigger` with the same rule: a drop of two or more bins sets `gTriggerAway` immediately; a one-bin drop increments `gTriggerRetreatFrames` and sets `gTriggerAway` at 2; any other peak change zeros `gTriggerRetreatFrames`. Latch on `gTriggerAway`. For the past-tee peak, increment `gTriggerDepartFrames` only on a passing frame and latch when it reaches 2. Zero it when the past-tee check fails.

- [ ] **Step 4: Replay the saved hits**

Run: `uv run pytest tests/test_iwr6843_self_trigger.py -q --tb=line`

Expected: the synthetic tests PASS. `test_saved_dumps_fire_when_the_ball_leaves` must still fire on every dump in `C:\Users\corma\Desktop\OF Sessions\iwr6843`. If any saved hit stops firing, revert the hysteresis and report the dump names. Do not weaken that test.

- [ ] **Step 5: Commit only if Step 4 kept every saved hit**

```bash
git add firmware/iwr6843/l3_dump.c src/openflight/iwr6843/self_trigger.py tests/test_iwr6843_self_trigger.py
git commit -m "$(cat <<'EOF'
fix(iwr6843): hold the ball-leave trigger across a one-bin wobble

A single-bin peak step was enough to freeze the ring.
EOF
)"
```

---

### Task 3: Measure the dense-profile rearm budget — change code only if frames are missed

**Files:**
- Modify, only if the measurement fails: `firmware/iwr6843/l3_dump.c` (`l3_considerSelfTrigger` bin loops, about lines 2913-2958)
- Test, only if the measurement fails: `tests/test_iwr6843_firmware_sparse.py`

**Gate:** Run this on the flashed Task 1 image, kiosk stopped, dense config:

```bash
uv run python - <<'EOF'
import time
from openflight.iwr6843.driver import IWR6843Radar
r = IWR6843Radar(port="/dev/ttyUSB0")
r.send_config("config/iwr6843_l3dump_dense_45f2ms_53bin_iq8.cfg")
r.cmd("triggerCfg 14 1000 2")
before = r.stats()
time.sleep(2)
after = r.stats()
print(before)
print(after)
r.close()
EOF
```

Pass: `hwa_missed` increases by 0 or 1 across the two seconds, and `hwa_rearms` increases by about 1000. Stop. Do not edit the power loops.

Fail: `hwa_missed` increases by more than 1. Then replace the three separate `l3_verticalPowerAt` walks (tee, approach bins, past-tee bins) with one function that fills a `float power[L3_MAX_BINS]` for the closed interval `[first, pastEnd)` in a single sample walk, and make `l3_considerSelfTrigger` read that array. Add a source test that `l3_considerSelfTrigger` contains one call to that function and no longer calls `l3_verticalPowerAt` inside the approach or past-tee loops. Re-run the dense measurement after the rebuild. The wide-profile `hwa_missed` behavior must stay at the Task 1 baseline.

---

## Self-review

- Task 1 covers the observability recommendation. Tasks 2 and 3 are the other two recommendations and cannot start from Task 1's commit.
- `stop_sensor` keeps working because `active=0` is unchanged and the new line is appended.
- The full-ring UART dump is intentionally absent. `l3sparse` / `l3track` already cover shot latency.
