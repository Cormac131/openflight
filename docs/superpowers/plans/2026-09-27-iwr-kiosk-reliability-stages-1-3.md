# IWR Kiosk Reliability (Stages 1–3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reproducible, calibrated, deadlock-free IWR-triggered kiosk with exactly one trigger owner per mode, preserving the onboard calculation pipeline.

**Architecture:**
- **Configuration.** A pure `kiosk_config` module resolves a named JSON profile, the CLI overrides and the firmware capabilities into one validated `ResolvedKioskConfig`, which is logged at `session_start`.
- **OPS serial.** OPS reception moves to one shared bounded-blocking reader that returns a structured `DumpRead`. OPS writes (`S!`, rearm) go through one lock, gated by an armed flag.
- **IWR readback.** Readback raises typed `IncompleteDumpError`. A bounded recovery routine runs before the monitor re-arms.
- **Trigger events.** Each accepted IWR event carries a monotonic `TriggerTimeline`.

**Tech Stack:** Python 3.11+ (`uv`), pytest, pyserial, Flask-SocketIO server, TI R4F firmware in C (pure modules compiled on the host by `firmware_host.py`).

**Spec:** `docs/superpowers/specs/2026-09-27-iwr-kiosk-reliability.md`

## Global Constraints

- Always run Python through `uv run` (`uv run pytest ...`, `uv run pylint ...`). Never bare `python`/`pytest`.
- Lint: `uv run pylint src/openflight/ --fail-under=9`, `uv run ruff check src/openflight/`, `uv run ruff format --check src/openflight/`.
- Every bug fix starts with a failing test that reproduces it (CLAUDE.md).
- Durations and deadlines use `time.monotonic()`. `time.time()` only for wall-clock record timestamps and cross-device matching (existing `trigger_timestamp` / `impact_timestamp` stay wall clock).
- Never subtract firmware ticks from host timestamps.
- Never retry `l3dump` after a failed transfer: the firmware has already re-armed.
- **Default capture profile.** It is weawer's 2 ms config, ported verbatim as `config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg`. `--iwr6843-config` defaults to it, and the kiosk profile `iq16-2ms` names it. The previous wide 3 ms config stays as the `wide-3ms` profile. (User decision 2026-09-27, overriding the pasted plan's "not the default yet".)
- Keep existing behaviour for users without `--iwr6843` (OPS + sound gate): the `sound_gate` trigger mode is the default when no IWR self-trigger is configured.
- Do not add new binary fixtures to the repo.
- Match surrounding code style: docstrings that say *why*, `logger` with `[OPS]` / `[IWR6843]` / `[SERVER]` prefixes.
- Commit after each task with a message ending `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- The working tree has unrelated uncommitted edits in `src/openflight/iwr6843/firmware_checks.py`, `src/openflight/iwr6843/runtime.py`, `tests/test_iwr6843_firmware_checks.py`, `tests/test_iwr6843_runtime_club.py`. Never `git add -A`; stage only files your task touches. Task 2 touches `firmware_checks.py`: stage only your hunks with `git add -p`.

## Review Focus

1. **The OPS reader is entered with `serial.timeout = None`.** The reader must still return by its deadline, and must restore `None` afterwards. Test in Task 6.
2. **`S!` is requested while the OPS is mid-rearm (between drain and `reset_input_buffer`).** It must be refused with `not_armed` and recorded, never written into a reset. Test in Task 8.
3. **A dump stalls past tolerance, then the rest of its bytes arrive during recovery.** Recovery must drain them. The next capture must not start with stale bytes, and the monitor must not show ready until the health check passes. Test in Task 10.
4. **The tee is inside the first window but its 12-bin approach region is not** (a short tee on a profile whose window starts at bin 20). Startup must refuse, naming the tee bin, the window and the fix. Test in Task 2.
5. **Old firmware without `stats caps` runs the default 2 ms `adaptive16` profile.** It must start, logging the build as `unknown` with a warning that `adaptive16` support could not be verified. A known build without `adaptive16` must be refused, naming the `wide-3ms` fallback. Tests in Tasks 4 and 5.

---

## File Structure

| File | Responsibility |
|---|---|
| `tests/serial_fakes.py` (new) | `FakeClock`, `VirtualSerial`, `WouldBlockForever`: deterministic pyserial stand-ins |
| `tests/test_serial_fakes.py` (new) | Tests for the fakes themselves |
| `src/openflight/iwr6843/calibration.py` | Adds `Calibration.apparent_range()`, the inverse of `true_range()` |
| `src/openflight/iwr6843/monitor.py` | `tee_global_bin(..., *, range_bias_m)`, `trigger_watch_bins()`, capture monitor: timeline, recovery, bounded captures, caps check hook |
| `firmware/iwr6843/l3_caps.{c,h}` (new) | Pure-C capability line formatter |
| `firmware/iwr6843/l3_dump.c`, `makefile` | `stats caps` sub-mode, `L3_BUILD_ID` define |
| `src/openflight/iwr6843/firmware_host.py` | Host binding for `l3_caps_format` |
| `src/openflight/iwr6843/caps.py` (new) | `FirmwareCaps`, `parse_caps()` |
| `src/openflight/iwr6843/driver.py` | `capabilities()`, `IncompleteDumpError`, stall tolerance, injectable clock |
| `src/openflight/kiosk_config.py` (new) | `KioskProfile`, `load_profile`, `TriggerMode`, `resolve_kiosk_config`, `check_firmware_compat`, `ResolvedKioskConfig` |
| `config/kiosk/iq16-2ms.json` (default), `config/kiosk/wide-3ms.json` (new) | Named profiles |
| `config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg` (ported) | Default 2 ms adaptive16 capture |
| `src/openflight/ops243.py` | `DumpRead`, `_read_iq_dump`, OPS I/O lock and armed flag, `RequestResult`, ST/SM |
| `src/openflight/rolling_buffer/{processor,trigger,monitor,types}.py` | Sample-count check, incomplete-dump rejection, ST/SM pass-through, first-byte monotonic |
| `src/openflight/trigger_timeline.py` (new) | `TriggerTimeline` dataclass and derived durations |
| `src/openflight/session_logger.py` | `log_trigger_timeline`, `log_iwr_capture_unmatched` |
| `src/openflight/server.py` | Profile two-pass parse, ST/SM flags, mode wiring, session logging |
| `scripts/start-kiosk.sh`, `scripts/hardware-test/set_cpu_governor.sh` (new) | Governor warning, manual governor tool |

---

### Task 1: Deterministic serial test fakes

**Files:**
- Create: `tests/serial_fakes.py`
- Test: `tests/test_serial_fakes.py`

**Interfaces:**
- Produces:
  - `FakeClock(start: float = 1000.0)`: `.monotonic() -> float`, `.time() -> float` (wall clock = `1.7e9 + monotonic`), `.sleep(s: float) -> None`, `.advance(s: float) -> None`.
  - `VirtualSerial(clock: FakeClock, *, timeout: float | None = 1.0)`:
    - attributes `.is_open`, `.timeout`, `.writes: list[bytes]`, `.resets: int`
    - methods `.feed(at: float, data: bytes)` (absolute clock time), `.feed_after(delay_s, data)`, `.in_waiting`, `.read(n)`, `.write(b)`, `.flush()`, `.reset_input_buffer()`, `.close()`
    - `.on_write: Callable[[bytes], None] | None`
  - `WouldBlockForever(AssertionError)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_serial_fakes.py
"""The fakes model pyserial's timeout semantics so reader loops can be tested without sleeping."""

import pytest

from tests.serial_fakes import FakeClock, VirtualSerial, WouldBlockForever


def test_read_returns_available_bytes_without_advancing_time():
    clock = FakeClock()
    port = VirtualSerial(clock)
    port.feed(clock.monotonic(), b"abc")
    assert port.read(2) == b"ab"
    assert port.in_waiting == 1
    assert clock.monotonic() == 1000.0


def test_read_blocks_up_to_timeout_when_nothing_arrives():
    clock = FakeClock()
    port = VirtualSerial(clock, timeout=0.05)
    assert port.read(1) == b""
    assert clock.monotonic() == pytest.approx(1000.05)


def test_read_wakes_at_the_next_arrival_inside_the_timeout():
    clock = FakeClock()
    port = VirtualSerial(clock, timeout=0.5)
    port.feed_after(0.1, b"x")
    assert port.read(1) == b"x"
    assert clock.monotonic() == pytest.approx(1000.1)


def test_timeout_none_with_nothing_scheduled_is_a_detectable_hang():
    clock = FakeClock()
    port = VirtualSerial(clock, timeout=None)
    with pytest.raises(WouldBlockForever):
        port.read(1)


def test_timeout_none_waits_for_a_scheduled_arrival():
    clock = FakeClock()
    port = VirtualSerial(clock, timeout=None)
    port.feed_after(3.0, b"y")
    assert port.read(1) == b"y"
    assert clock.monotonic() == pytest.approx(1003.0)


def test_reset_input_buffer_drops_arrived_bytes_only():
    clock = FakeClock()
    port = VirtualSerial(clock)
    port.feed(clock.monotonic(), b"old")
    port.feed_after(1.0, b"new")
    port.reset_input_buffer()
    assert port.in_waiting == 0
    assert port.resets == 1
    clock.advance(1.0)
    assert port.read(10) == b"new"


def test_writes_are_recorded_and_can_schedule_replies():
    clock = FakeClock()
    port = VirtualSerial(clock)
    port.on_write = lambda data: port.feed_after(0.01, b"ok") if data == b"S!\r" else None
    port.write(b"S!\r")
    assert port.writes == [b"S!\r"]
    assert port.read(2) == b"ok"


def test_wall_clock_tracks_monotonic():
    clock = FakeClock()
    clock.sleep(2.5)
    assert clock.time() - clock.monotonic() == pytest.approx(1.7e9)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_serial_fakes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tests.serial_fakes'`. If `tests` isn't importable as a package, check whether `tests/__init__.py` exists (`ls tests/__init__.py`). If it doesn't, import as `from serial_fakes import ...`, matching how `tests/iwr6843_fakes.py` is imported elsewhere (`grep -rn "iwr6843_fakes import" tests | head -1`), and use that style throughout this plan.

- [ ] **Step 3: Implement**

```python
# tests/serial_fakes.py
"""Deterministic pyserial stand-ins driven by a fake clock.

``read(n)`` behaves like pyserial: it returns what has arrived, otherwise it
waits up to ``timeout`` (advancing the fake clock) for the next scheduled
byte. With ``timeout=None`` and nothing scheduled, real pyserial would block
forever; the fake raises ``WouldBlockForever`` so that hang is a test failure.
"""

from __future__ import annotations

import bisect
from typing import Callable

_WALL_OFFSET_S = 1.7e9


class WouldBlockForever(AssertionError):
    """A read with timeout=None and no bytes ever coming."""


class FakeClock:
    """Monotonic and wall clocks that only move when told to."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = float(start)

    def monotonic(self) -> float:
        return self._now

    def time(self) -> float:
        return _WALL_OFFSET_S + self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("clock cannot go backwards")
        self._now += seconds

    def sleep(self, seconds: float) -> None:
        self.advance(max(0.0, seconds))


class VirtualSerial:
    """Scripted serial port: bytes become readable at their scheduled time."""

    def __init__(self, clock: FakeClock, *, timeout: float | None = 1.0) -> None:
        self.clock = clock
        self.timeout = timeout
        self.is_open = True
        self.writes: list[bytes] = []
        self.resets = 0
        self.on_write: Callable[[bytes], None] | None = None
        self._times: list[float] = []
        self._chunks: list[bytes] = []
        self._buffer = bytearray()

    def feed(self, at: float, data: bytes) -> None:
        index = bisect.bisect_right(self._times, at)
        self._times.insert(index, at)
        self._chunks.insert(index, bytes(data))

    def feed_after(self, delay_s: float, data: bytes) -> None:
        self.feed(self.clock.monotonic() + delay_s, data)

    def _arrive(self) -> None:
        now = self.clock.monotonic()
        while self._times and self._times[0] <= now:
            self._times.pop(0)
            self._buffer.extend(self._chunks.pop(0))

    @property
    def in_waiting(self) -> int:
        self._arrive()
        return len(self._buffer)

    def read(self, size: int = 1) -> bytes:
        self._arrive()
        if not self._buffer:
            if self.timeout is None:
                if not self._times:
                    raise WouldBlockForever("read() with timeout=None and no data scheduled")
                self.clock.advance(self._times[0] - self.clock.monotonic())
            else:
                next_at = self._times[0] if self._times else None
                wait = self.timeout
                if next_at is not None:
                    wait = min(wait, max(0.0, next_at - self.clock.monotonic()))
                self.clock.advance(wait)
            self._arrive()
        chunk = bytes(self._buffer[:size])
        del self._buffer[:size]
        return chunk

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        if self.on_write is not None:
            self.on_write(bytes(data))
        return len(data)

    def flush(self) -> None:
        return None

    def reset_input_buffer(self) -> None:
        self._arrive()
        self._buffer.clear()
        self.resets += 1

    def close(self) -> None:
        self.is_open = False
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_serial_fakes.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add tests/serial_fakes.py tests/test_serial_fakes.py
git commit -m "tests: deterministic VirtualSerial and FakeClock with pyserial timeout semantics"
```

---

### Task 2: One range convention for trigger placement (spec 2A, decision 3A)

**Files:**
- Modify: `src/openflight/iwr6843/calibration.py` (add `apparent_range`)
- Modify: `src/openflight/iwr6843/monitor.py:109-130` (`tee_global_bin`), add `trigger_watch_bins` and the two approach/gate constants
- Modify: `src/openflight/server.py:1107-1142` (`_self_trigger_config`)
- Modify: `src/openflight/iwr6843/shot.py:119`, `src/openflight/iwr6843/recovery.py:104,147` (use `apparent_range`)
- Modify: `src/openflight/iwr6843/firmware_checks.py:264-266` (`expected_tee_bin`), plus the `Context` dataclass at `:174`
- Modify: `scripts/iwr6843/watch_trigger.py:40`, `scripts/iwr6843/swing_trigger.py:417`
- Test: `tests/test_iwr6843_monitor.py` (next to the existing `tee_global_bin` tests at `:888`), `tests/test_server.py` (self-trigger config tests near `:5072`), `tests/test_iwr6843_calibration_range.py` (new)

**Interfaces:**
- Produces:
  - `Calibration.apparent_range(true_m: float) -> float`
  - `tee_global_bin(tee_range_m, config_path, fft_size=128, *, range_bias_m: float) -> int` (keyword is **required**)
  - `trigger_watch_bins(tee_bin: int, config_path) -> tuple[int, int]`: returns `(first_bin, last_bin)` of the watch region, or raises `ValueError` naming the window and the fix
  - `SELF_TRIGGER_APPROACH_BINS = 12`, `SELF_TRIGGER_GATE_BINS = 3` in `monitor.py`
  - `server._self_trigger_config(args) -> SelfTriggerConfig | None`: unchanged signature; now biased

Numbers used in the tests. Bins are 6.0/128 = 0.046875 m. The reference calibration's `range_bias_const_m` = 0.066007.
- Unbiased tee 1.575 m → 33.6 → bin **34**.
- Biased tee 1.575 + 0.066 = 1.641 → 35.01 → bin **35**.
- Bias −0.066 gives 1.509 → 32.19 → bin **32**.
- The wide profile's first window covers bins 20–72.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_iwr6843_calibration_range.py
"""apparent_range is the one tee->radar-bin convention; true_range inverts it."""

import pytest

from openflight.iwr6843.calibration import Calibration


def test_apparent_range_adds_the_bias_and_true_range_inverts_it():
    cal = Calibration.identity()
    cal.range_bias_m = 0.066
    assert cal.apparent_range(1.575) == pytest.approx(1.641)
    assert cal.true_range(cal.apparent_range(1.575)) == pytest.approx(1.575)


def test_negative_bias_moves_the_apparent_range_closer():
    cal = Calibration.identity()
    cal.range_bias_m = -0.066
    assert cal.apparent_range(1.575) == pytest.approx(1.509)
```

Add to `tests/test_iwr6843_monitor.py`, next to the existing `tee_global_bin` tests. Reuse the existing tests' cfg path/fixture:

```python
WIDE_CFG = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"  # first window bins 20-72


@pytest.mark.parametrize(
    ("bias_m", "expected_bin"),
    [(0.0, 34), (0.066, 35), (-0.066, 32)],
)
def test_tee_global_bin_applies_the_range_bias(bias_m, expected_bin):
    assert tee_global_bin(1.575, WIDE_CFG, range_bias_m=bias_m) == expected_bin


def test_tee_global_bin_requires_an_explicit_bias():
    with pytest.raises(TypeError):
        tee_global_bin(1.575, WIDE_CFG)  # pylint: disable=missing-kwoa


def test_tee_global_bin_rejects_a_biased_tee_past_the_window_end():
    # bin 72 is the last bin in the window: 72 * 0.046875 = 3.375 m apparent.
    assert tee_global_bin(3.375 - 0.066, WIDE_CFG, range_bias_m=0.066) == 72
    with pytest.raises(ValueError, match="outside the first capture window"):
        tee_global_bin(3.375 + 0.047 - 0.066, WIDE_CFG, range_bias_m=0.066)


def test_watch_region_covers_approach_and_gate():
    assert trigger_watch_bins(35, WIDE_CFG) == (35 - 12, 35 + 3)


def test_watch_region_refuses_an_approach_that_starts_before_the_window():
    # tee bin 30: approach would start at 18, window starts at 20.
    with pytest.raises(ValueError) as excinfo:
        trigger_watch_bins(30, WIDE_CFG)
    message = str(excinfo.value)
    assert "bin 30" in message and "20-72" in message and "--iwr6843-tee-m" in message


def test_watch_region_refuses_a_gate_past_the_window_end():
    with pytest.raises(ValueError, match="gate"):
        trigger_watch_bins(71, WIDE_CFG)


def test_watch_region_accepts_the_exact_window_edges():
    assert trigger_watch_bins(32, WIDE_CFG) == (20, 35)
    assert trigger_watch_bins(69, WIDE_CFG) == (57, 72)
```

Add to `tests/test_server.py`, next to the existing `_self_trigger_config` tests. Reuse their args-namespace builder; if it has no `iwr6843_cal` field, add one defaulting to `"config/iwr6843_calibration_reference.json"`. Make the builder pass `iwr6843_config=WIDE_CFG` explicitly, because Task 5 changes the CLI default cfg and these tests pin wide-window bins:

```python
def test_self_trigger_bin_uses_calibration_range_bias(tmp_path):
    cal = tmp_path / "cal.json"
    reference = json.loads(Path("config/iwr6843_calibration_reference.json").read_text())
    reference["range_bias_const_m"] = 0.066
    cal.write_text(json.dumps(reference))
    args = _self_trigger_args(iwr6843_cal=str(cal), iwr6843_tee_m=1.575)
    assert server._self_trigger_config(args).tee_bin == 35


def test_explicit_self_trigger_bin_is_a_raw_override(tmp_path):
    cal = tmp_path / "cal.json"
    reference = json.loads(Path("config/iwr6843_calibration_reference.json").read_text())
    reference["range_bias_const_m"] = 0.5  # would move the tee ~11 bins if applied
    cal.write_text(json.dumps(reference))
    args = _self_trigger_args(iwr6843_cal=str(cal), iwr6843_self_trigger_bin=34)
    assert server._self_trigger_config(args).tee_bin == 34


def test_self_trigger_refuses_a_tee_whose_approach_leaves_the_window():
    args = _self_trigger_args(iwr6843_tee_m=1.35)  # biased bin 30, approach from 18
    with pytest.raises(ValueError, match="approach"):
        server._self_trigger_config(args)


def test_follow_mode_arms_the_biased_initial_bin_and_sends_no_bias_later():
    """Follow mode: the firmware re-aims at the bin it measured; the host never re-biases it."""
    args = _self_trigger_args(iwr6843_tee_m=1.575, iwr6843_ball_detector="follow")
    assert server._self_trigger_config(args).tee_bin == 35
    # The follow flag goes to the firmware as-is; no bin value accompanies it.
    assert IWR6843Radar.configure_ball.__doc__ is not None
```

For the follow-mode host side, also add to `tests/test_iwr6843_driver.py`:

```python
def test_follow_mode_sends_no_bin_so_no_host_bias_can_be_applied():
    port = FakeSerial(responses={"ball cfg 1 1": "Done\n"})  # use the file's FakeSerial style
    radar = _radar_on(port)                                    # existing helper in this file
    radar.configure_ball(True, True)
    assert port.written_lines() == ["ball cfg 1 1"]
```

If the `FakeSerial` helpers in `test_iwr6843_driver.py` are named differently, adapt to the file's existing helper for `configure_ball` (driver.py:557-561). The assertion that matters: the command carries only the enable/follow flags.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_iwr6843_calibration_range.py tests/test_iwr6843_monitor.py -k "tee_global_bin or watch_region" tests/test_server.py -k "self_trigger" -v`
Expected: FAIL. Look for `AttributeError: apparent_range`, `ImportError: trigger_watch_bins`, and the bias tests returning 34 instead of 35.

- [ ] **Step 3: Implement**

`calibration.py`, below `true_range`:

```python
    def apparent_range(self, true_m: float) -> float:
        """Where the radar reports a target that is really at ``true_m``.

        The one convention for turning a tape-measured distance into radar
        bins: trigger placement, window checks and the shot fits all use it,
        so they agree on which bin the tee is. Inverse of ``true_range``.
        """
        return true_m + self.range_bias_m
```

`monitor.py`:

```python
# Mirrors L3_TRIG_DEFAULT_APPROACH_BINS / L3_TRIG_DEFAULT_GATE_BINS in
# firmware/iwr6843/l3_trigger.h (tests/test_iwr6843_monitor.py pins them).
SELF_TRIGGER_APPROACH_BINS = 12
SELF_TRIGGER_GATE_BINS = 3


def tee_global_bin(
    tee_range_m: float, config_path: str | Path, fft_size: int = 128, *, range_bias_m: float
) -> int:
    """The tee's global range-FFT bin, checked to lie in the cfg's first window.

    ``range_bias_m`` is required: the radar reports the tee ~1.4 bins beyond
    its tape-measured distance (``Calibration.range_bias_m``), and a trigger
    armed on the unbiased bin watches short of the ball. Pass 0.0 only when
    the distance is already an apparent (radar-measured) range.
    """
    summary = read_capture_config(config_path)
    if summary.first_window_start is None or summary.first_window_bins is None:
        raise ValueError(f"{config_path} has no phaseCaptureCfg")
    apparent_m = tee_range_m + range_bias_m
    absolute = int(round(apparent_m / (RANGE_SPAN_M / fft_size)))
    last = summary.first_window_start + summary.first_window_bins - 1
    if not summary.first_window_start <= absolute <= last:
        raise ValueError(
            f"tee at {tee_range_m:.2f} m (apparent {apparent_m:.3f} m, bin {absolute}) is "
            f"outside the first capture window, bins {summary.first_window_start}-{last}"
        )
    return absolute


def trigger_watch_bins(tee_bin: int, config_path: str | Path) -> tuple[int, int]:
    """(first, last) bins the firmware trigger watches, checked against the first window.

    The detector follows the club over ``SELF_TRIGGER_APPROACH_BINS`` short of
    the tee and fires in a gate ``SELF_TRIGGER_GATE_BINS`` either side of it;
    the firmware only has samples inside the first capture window.
    """
    summary = read_capture_config(config_path)
    if summary.first_window_start is None or summary.first_window_bins is None:
        raise ValueError(f"{config_path} has no phaseCaptureCfg")
    start = summary.first_window_start
    last = start + summary.first_window_bins - 1
    first_watch = tee_bin - SELF_TRIGGER_APPROACH_BINS
    last_watch = tee_bin + SELF_TRIGGER_GATE_BINS
    if first_watch < start:
        raise ValueError(
            f"self-trigger at bin {tee_bin} needs its approach from bin {first_watch}, but "
            f"{Path(config_path).name} captures bins {start}-{last}: move the tee out "
            f"(--iwr6843-tee-m) or use a profile whose first window starts at or before "
            f"bin {first_watch}"
        )
    if last_watch > last:
        raise ValueError(
            f"self-trigger gate at bin {tee_bin} reaches bin {last_watch}, past the first "
            f"window's bins {start}-{last} in {Path(config_path).name}: move the tee in "
            f"(--iwr6843-tee-m) or widen the first window"
        )
    return first_watch, last_watch
```

Add a test that pins the constants to the firmware header, in `tests/test_iwr6843_monitor.py`:

```python
def test_watch_constants_match_firmware_defaults():
    header = Path("firmware/iwr6843/l3_trigger.h").read_text()
    assert f"L3_TRIG_DEFAULT_APPROACH_BINS {SELF_TRIGGER_APPROACH_BINS}U" in header
    assert f"L3_TRIG_DEFAULT_GATE_BINS     {SELF_TRIGGER_GATE_BINS}U" in header
```

`server._self_trigger_config`, replacing the `if bin_index is None:` block:

```python
    bin_index = args.iwr6843_self_trigger_bin
    if bin_index is None:
        # An explicit --iwr6843-self-trigger-bin is already a radar bin and is
        # used as given. A tee distance is a tape measure: map it through the
        # calibration's range bias, as the shot fits do.
        calibration = Calibration.load(args.iwr6843_cal)
        bin_index = tee_global_bin(
            args.iwr6843_tee_m, args.iwr6843_config, range_bias_m=calibration.range_bias_m
        )
    trigger_watch_bins(bin_index, args.iwr6843_config)
```

Add `trigger_watch_bins` to the function's local import from `.iwr6843.monitor`, and `from .iwr6843.calibration import Calibration`.

`shot.py:119`: `apparent_tee_range_m = tee_range_m + range_bias_m` stays arithmetic because that function gets plain floats. Leave it. `recovery.py:104` and `:147`: replace `calibration.tee_range_m + calibration.range_bias_m` with `calibration.apparent_range(calibration.tee_range_m)`.

`firmware_checks.py`: add a required field `range_bias_m: float` directly after `tee_m: float` in `Context`, then:

```python
def expected_tee_bin(ctx: Context) -> int:
    """The global range bin ``--tee-m`` converts to, checked against this profile's window."""
    return tee_global_bin(ctx.tee_m, ctx.config, range_bias_m=ctx.range_bias_m)
```

Find every `Context(` construction (`grep -rn "Context(" src scripts tests | grep -v "class Context"`). Pass `range_bias_m=Calibration.load(args.cal).range_bias_m` in scripts, adding a `--cal` argument with default `DEFAULT_CAL_PATH` where the script lacks one. Pass `range_bias_m=0.0` in test helpers, which keeps their existing bin expectations.

`scripts/iwr6843/watch_trigger.py` and `swing_trigger.py`: add `parser.add_argument("--cal", default=DEFAULT_CAL_PATH)`, then `tee_global_bin(args.tee_m, args.config, range_bias_m=Calibration.load(args.cal).range_bias_m)`.

- [ ] **Step 4: Run to verify they pass, plus every existing test touching these paths**

Run: `uv run pytest tests/test_iwr6843_calibration_range.py tests/test_iwr6843_monitor.py tests/test_server.py tests/test_iwr6843_tee_scan.py tests/test_iwr6843_late_window.py tests/test_iwr6843_firmware_checks.py tests/test_iwr6843_recovery.py tests/test_iwr6843_driver.py -q`
Expected: all pass. Existing tests that call `tee_global_bin` without the keyword must be updated to pass `range_bias_m=0.0`, which keeps their expected bins.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/calibration.py src/openflight/iwr6843/monitor.py src/openflight/server.py src/openflight/iwr6843/recovery.py scripts/iwr6843/watch_trigger.py scripts/iwr6843/swing_trigger.py tests/test_iwr6843_calibration_range.py tests/test_iwr6843_monitor.py tests/test_server.py tests/test_iwr6843_tee_scan.py tests/test_iwr6843_late_window.py tests/test_iwr6843_driver.py
git add -p src/openflight/iwr6843/firmware_checks.py tests/test_iwr6843_firmware_checks.py   # only this task's hunks
git commit -m "iwr: one range convention for trigger placement, watch-region coverage check"
```

---

### Task 3: Firmware capability report (`stats caps`) and host parser (decision 2A)

**Files:**
- Create: `firmware/iwr6843/l3_caps.h`, `firmware/iwr6843/l3_caps.c`
- Modify: `firmware/iwr6843/l3_dump.c` (`l3_cli_stats` at `:4819`, help text of table entry 3), `firmware/iwr6843/makefile` (add source and build id)
- Modify: `src/openflight/iwr6843/firmware_host.py` (add `"l3_caps.c"` to the module list at `:28-42`, and a binding next to the `l3_text` bindings at `:841`)
- Create: `src/openflight/iwr6843/caps.py`
- Modify: `src/openflight/iwr6843/driver.py` (add `capabilities()` next to `stats()` at `:530`)
- Test: `tests/test_iwr6843_firmware_caps.py` (new), `tests/test_iwr6843_caps.py` (new)

**Interfaces:**
- Produces (C): `uint32_t l3_caps_format(const char *build, uint32_t formats, uint32_t features, char *out, uint32_t cap)`. It returns the length written, or 0 if `cap` is too small, in which case `out[0]` is set to `'\0'`.
  - Bit constants: `L3_CAPS_FMT_IQ16=1u<<0`, `_IQ8=1u<<1`, `_COMPACT16=1u<<2`, `_ADAPTIVE16=1u<<3`.
  - Feature constants: `L3_CAPS_FEAT_TRIGGER=1u<<0`, `_RELEASE=1u<<1`, `_TRACK=1u<<2`, `_BALL=1u<<3`, `_RESULT=1u<<4`, `_APPROACH=1u<<5`.
  - Output: `caps v=1 build=<build> formats=iq16,adaptive16 features=trigger,release`. A `-` stands for an empty set.
- Produces (Python):
  - `FirmwareCaps(known: bool, build: str, formats: frozenset[str], features: frozenset[str])`, with `FirmwareCaps.unknown()` and `.as_log() -> dict`.
  - `parse_caps(reply: str) -> FirmwareCaps`.
  - `IWR6843Radar.capabilities() -> FirmwareCaps`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_iwr6843_caps.py
"""Host parsing of the firmware's ``stats caps`` line; old firmware reads as unknown."""

from openflight.iwr6843.caps import FirmwareCaps, parse_caps


def test_parse_full_caps_line():
    caps = parse_caps(
        "caps v=1 build=776c266e1a2b formats=iq16,adaptive16 features=trigger,release,track\r\nDone\r\n"
    )
    assert caps == FirmwareCaps(
        known=True,
        build="776c266e1a2b",
        formats=frozenset({"iq16", "adaptive16"}),
        features=frozenset({"trigger", "release", "track"}),
    )


def test_dash_means_empty_set():
    caps = parse_caps("caps v=1 build=x formats=iq16 features=-\nDone\n")
    assert caps.features == frozenset()


def test_old_firmware_prints_plain_stats_and_reads_as_unknown():
    caps = parse_caps("frames=120 wraps=3 active=1 calib=0x0\nDone\n")
    assert caps == FirmwareCaps.unknown()
    assert not caps.known and caps.build == "unknown"


def test_newer_caps_version_keeps_known_fields_and_ignores_extra_tokens():
    caps = parse_caps("caps v=2 build=b formats=iq16 features=trigger ring_kib=768\nDone\n")
    assert caps.known and caps.build == "b" and caps.features == {"trigger"}


def test_truncated_caps_line_without_build_is_unknown():
    assert parse_caps("caps v=1 formats=iq16\nDone\n") == FirmwareCaps.unknown()


def test_as_log_is_json_friendly_and_sorted():
    caps = parse_caps("caps v=1 build=b formats=iq8,iq16 features=release,trigger\nDone\n")
    assert caps.as_log() == {
        "known": True,
        "build": "b",
        "formats": ["iq16", "iq8"],
        "features": ["release", "trigger"],
    }
```

```python
# tests/test_iwr6843_firmware_caps.py
"""The C formatter the board runs for ``stats caps``, built for the host."""

import pytest

from openflight.iwr6843 import firmware_host

pytestmark = pytest.mark.skipif(
    not firmware_host.compiler_available(), reason="no host C compiler"
)

FMT_IQ16, FMT_IQ8, FMT_COMPACT16, FMT_ADAPTIVE16 = 1, 2, 4, 8
FEAT_TRIGGER, FEAT_RELEASE, FEAT_TRACK, FEAT_BALL, FEAT_RESULT, FEAT_APPROACH = 1, 2, 4, 8, 16, 32


def test_formats_every_flag_in_a_fixed_order():
    line = firmware_host.caps_format(
        "abc123", FMT_IQ16 | FMT_ADAPTIVE16, FEAT_TRIGGER | FEAT_RELEASE | FEAT_APPROACH
    )
    assert line == "caps v=1 build=abc123 formats=iq16,adaptive16 features=trigger,release,approach"


def test_empty_sets_print_a_dash():
    assert firmware_host.caps_format("b", FMT_IQ16, 0) == "caps v=1 build=b formats=iq16 features=-"


def test_too_small_buffer_returns_empty_not_a_truncated_line():
    assert firmware_host.caps_format("b", FMT_IQ16, FEAT_TRIGGER, cap=16) == ""


def test_host_parser_round_trips_the_c_output():
    from openflight.iwr6843.caps import parse_caps

    line = firmware_host.caps_format("b", FMT_IQ16 | FMT_IQ8, FEAT_BALL | FEAT_RESULT)
    caps = parse_caps(line + "\nDone\n")
    assert caps.formats == {"iq16", "iq8"} and caps.features == {"ball", "result"}
```

Before writing these, check how existing firmware-host tests skip when no compiler is present and how they call bound functions: `grep -n "skipif\|compiler" tests/test_iwr6843_firmware_trigger.py | head`. Use the same skip marker and call style. If `firmware_host` exposes `load()` or a library object instead of module-level helpers, add `caps_format` in that same style.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_iwr6843_caps.py tests/test_iwr6843_firmware_caps.py -v`
Expected: FAIL (`No module named openflight.iwr6843.caps`, `caps_format` missing).

- [ ] **Step 3: Implement the C module**

```c
/* firmware/iwr6843/l3_caps.h
 * Capability line for "stats caps": which build this is and what it can do,
 * so the host can refuse a kiosk profile the flashed firmware cannot run.
 * Pure C. */
#ifndef L3_CAPS_H
#define L3_CAPS_H

#include <stdint.h>

#ifndef L3_BUILD_ID
#define L3_BUILD_ID "unknown"
#endif

#define L3_CAPS_FMT_IQ16        (1U << 0)
#define L3_CAPS_FMT_IQ8         (1U << 1)
#define L3_CAPS_FMT_COMPACT16   (1U << 2)
#define L3_CAPS_FMT_ADAPTIVE16  (1U << 3)

#define L3_CAPS_FEAT_TRIGGER    (1U << 0)  /* triggerCfg self-trigger */
#define L3_CAPS_FEAT_RELEASE    (1U << 1)  /* l3release */
#define L3_CAPS_FEAT_TRACK      (1U << 2)  /* l3track / trackCfg */
#define L3_CAPS_FEAT_BALL       (1U << 3)  /* ball placement detector */
#define L3_CAPS_FEAT_RESULT     (1U << 4)  /* triggerLog result packet */
#define L3_CAPS_FEAT_APPROACH   (1U << 5)  /* triggerCfg minApproachBins */

/* "caps v=1 build=<build> formats=<a,b> features=<c,d>" ("-" for none).
 * Returns the length written, or 0 with out[0] = '\0' when cap is too small:
 * a truncated line would parse as a smaller capability set. */
uint32_t l3_caps_format(const char *build, uint32_t formats, uint32_t features,
                        char *out, uint32_t cap);

#endif /* L3_CAPS_H */
```

```c
/* firmware/iwr6843/l3_caps.c */
#include "l3_caps.h"

static const char *const kFormatNames[] = {"iq16", "iq8", "compact16", "adaptive16"};
static const char *const kFeatureNames[] = {"trigger", "release", "track",
                                            "ball", "result", "approach"};

static int32_t l3_caps_append(char *out, uint32_t cap, uint32_t *len, const char *text)
{
    while (*text != '\0') {
        if (*len + 1U >= cap) {
            return -1;
        }
        out[(*len)++] = *text++;
    }
    out[*len] = '\0';
    return 0;
}

static int32_t l3_caps_list(char *out, uint32_t cap, uint32_t *len, uint32_t bits,
                            const char *const *names, uint32_t count)
{
    uint32_t i;
    uint32_t written = 0U;
    for (i = 0U; i < count; i++) {
        if ((bits & (1U << i)) == 0U) {
            continue;
        }
        if (written > 0U && l3_caps_append(out, cap, len, ",") != 0) {
            return -1;
        }
        if (l3_caps_append(out, cap, len, names[i]) != 0) {
            return -1;
        }
        written++;
    }
    return (written == 0U) ? l3_caps_append(out, cap, len, "-") : 0;
}

uint32_t l3_caps_format(const char *build, uint32_t formats, uint32_t features,
                        char *out, uint32_t cap)
{
    uint32_t len = 0U;
    if (out == 0 || cap == 0U) {
        return 0U;
    }
    out[0] = '\0';
    if (l3_caps_append(out, cap, &len, "caps v=1 build=") != 0
        || l3_caps_append(out, cap, &len, build) != 0
        || l3_caps_append(out, cap, &len, " formats=") != 0
        || l3_caps_list(out, cap, &len, formats, kFormatNames, 4U) != 0
        || l3_caps_append(out, cap, &len, " features=") != 0
        || l3_caps_list(out, cap, &len, features, kFeatureNames, 6U) != 0) {
        out[0] = '\0';
        return 0U;
    }
    return len;
}
```

`firmware_host.py`:
- Add `"l3_caps.c"` to the module list.
- Add the binding `"l3_caps_format": ([ctypes.c_char_p, _U32, _U32, *_TEXT], _U32)` next to the `l3_text` bindings.
- Add a helper in the same style as the other public helpers in that file:

```python
def caps_format(build: str, formats: int, features: int, cap: int = 160) -> str:
    """Run the firmware's ``l3_caps_format`` and return its line ('' when it did not fit)."""
    out = ctypes.create_string_buffer(cap)
    _lib().l3_caps_format(build.encode(), formats, features, out, cap)
    return out.value.decode()
```

(Use the file's existing library accessor in place of `_lib()`; find it with `grep -n "def _lib\|CDLL" src/openflight/iwr6843/firmware_host.py`.)

`l3_dump.c`: add `#include "l3_caps.h"` with the other module includes, and add `l3_caps.c` wherever the makefile lists module sources (`grep -n "l3_text" firmware/iwr6843/makefile`). At the top of `l3_cli_stats`, replace `(void)argc; (void)argv;` with:

```c
    if (argc >= 2 && strcmp(argv[1], "caps") == 0) {
        static char line[160];
        uint32_t formats = L3_CAPS_FMT_IQ16;
        uint32_t features = 0U;
#ifdef L3_RING_IQ8
        /* l3_cli_captureFormat accepts all four keywords in this build. */
        formats |= L3_CAPS_FMT_IQ8 | L3_CAPS_FMT_COMPACT16 | L3_CAPS_FMT_ADAPTIVE16;
#endif
#ifdef HWA_CHAINED_SNAPSHOT_RING
        features |= L3_CAPS_FEAT_TRIGGER | L3_CAPS_FEAT_RELEASE | L3_CAPS_FEAT_TRACK
                  | L3_CAPS_FEAT_BALL | L3_CAPS_FEAT_RESULT | L3_CAPS_FEAT_APPROACH;
#endif
        if (l3_caps_format(L3_BUILD_ID, formats, features, line, sizeof(line)) == 0U) {
            CLI_write("Error: caps line too long\n");
            return -1;
        }
        CLI_write("%s\n", line);
        return 0;
    }
    (void)argc;
    (void)argv;
```

Before committing to those `#ifdef` groupings, confirm each feature's handler really is compiled under that guard: `grep -n "l3_cli_triggerCfg\|l3_cli_release\|l3_cli_track\|l3_cli_ball\|triggerLog result" firmware/iwr6843/l3_dump.c | head`. Set any feature not under `HWA_CHAINED_SNAPSHOT_RING` under its actual guard. Change the help string of table entry 3 to `"stats [caps]: capture counters, or build id and capabilities"`.

`makefile`, after `R4F_CFLAGS += $(L3_VARIANT_DEFS)`:

```make
# Build identifier reported by "stats caps": the commit, marked -dirty when the
# tree has local edits, so a session log names the exact firmware it ran.
L3_BUILD_ID ?= $(shell git -C $(CURDIR) rev-parse --short=12 HEAD 2>/dev/null || echo unknown)$(shell git -C $(CURDIR) diff --quiet 2>/dev/null || echo -dirty)
R4F_CFLAGS += --define=L3_BUILD_ID=\"$(L3_BUILD_ID)\"
```

- [ ] **Step 4: Implement the host parser and driver method**

```python
# src/openflight/iwr6843/caps.py
"""What the flashed IWR6843 firmware says it is and can do (``stats caps``).

Firmware from before the capability report ignores the argument and prints
plain ``stats``; that reads as an unknown build rather than an error, so an
older board still runs the default profile.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FirmwareCaps:
    known: bool
    build: str
    formats: frozenset[str]
    features: frozenset[str]

    @classmethod
    def unknown(cls) -> "FirmwareCaps":
        return cls(known=False, build="unknown", formats=frozenset(), features=frozenset())

    def as_log(self) -> dict:
        return {
            "known": self.known,
            "build": self.build,
            "formats": sorted(self.formats),
            "features": sorted(self.features),
        }


def _names(value: str) -> frozenset[str]:
    return frozenset() if value == "-" else frozenset(v for v in value.split(",") if v)


def parse_caps(reply: str) -> FirmwareCaps:
    """Parse the first ``caps v=`` line in a CLI reply."""
    for line in reply.splitlines():
        line = line.strip()
        if not line.startswith("caps v="):
            continue
        fields = dict(token.split("=", 1) for token in line.split()[1:] if "=" in token)
        if "build" not in fields or "formats" not in fields or "features" not in fields:
            return FirmwareCaps.unknown()
        return FirmwareCaps(
            known=True,
            build=fields["build"],
            formats=_names(fields["formats"]),
            features=_names(fields["features"]),
        )
    return FirmwareCaps.unknown()
```

`driver.py`, next to `stats()`:

```python
    def capabilities(self) -> FirmwareCaps:
        """``stats caps``: build id and capability flags (unknown on older firmware)."""
        return parse_caps(self.cmd("stats caps", 2.0))
```

Add a driver test in `tests/test_iwr6843_driver.py`, using the file's scripted-reply fake, which checks that `capabilities()` sends exactly `stats caps` and parses the reply.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_iwr6843_caps.py tests/test_iwr6843_firmware_caps.py tests/test_iwr6843_driver.py -v`
Expected: all pass. The firmware test skips only if there is no C compiler on the host; if it skips, say so in the commit message.

- [ ] **Step 6: Commit**

```bash
git add firmware/iwr6843/l3_caps.h firmware/iwr6843/l3_caps.c firmware/iwr6843/l3_dump.c firmware/iwr6843/makefile src/openflight/iwr6843/firmware_host.py src/openflight/iwr6843/caps.py src/openflight/iwr6843/driver.py tests/test_iwr6843_caps.py tests/test_iwr6843_firmware_caps.py tests/test_iwr6843_driver.py
git commit -m "iwr: stats caps reports build id and capabilities; host parses it, old firmware reads unknown"
```

Note for the human: `l3_dump.bin` is **not** rebuilt here. It must be rebuilt in the TI SDK container and reflashed before the build id shows up; until then the host logs `build=unknown`.

---

### Task 4: Kiosk profiles, trigger mode and pure resolver (decisions 1A, 2A, 4A, 12A)

**Files:**
- Create: `src/openflight/kiosk_config.py`
- Create: `config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg` (ported), `config/kiosk/iq16-2ms.json`, `config/kiosk/wide-3ms.json`
- Test: `tests/test_kiosk_config.py`

**Interfaces:**
- Consumes: `FirmwareCaps` (Task 3), `read_capture_config` (monitor.py).
- Produces:
  - `class TriggerMode(str, Enum)`: `SOUND_GATE = "sound_gate"`, `IWR_PRIMARY = "iwr_primary"`, `OPS_INDEPENDENT = "ops_independent"`.
  - `@dataclass(frozen=True) class KioskProfile`: `name: str`, `description: str`, `experimental: bool`, `requires_formats: frozenset[str]`, `requires_features: frozenset[str]`, `args: dict[str, object]`, `source: str`.
  - `load_profile(name_or_path: str, profiles_dir: Path = Path("config/kiosk")) -> KioskProfile`: raises `ValueError` on an unknown name, malformed JSON or missing keys.
  - `profile_arg_errors(profile: KioskProfile, known_dests: set[str]) -> list[str]`: unknown `args` keys.
  - `resolve_trigger_mode(*, explicit: str | None, iwr6843: bool, self_trigger: bool, ops_trigger_speed_mph: float | None, ops_trigger_magnitude: int | None, trigger_type: str) -> TriggerMode`: raises `ValueError` with an actionable message.
  - `@dataclass(frozen=True) class ResolvedKioskConfig`: `profile: str | None`, `profile_source: str | None`, `experimental: bool`, `trigger_mode: TriggerMode`, `overrides: dict[str, dict]`, `requires_formats: frozenset[str]`, `requires_features: frozenset[str]`, `capture_format: str | None`, `settings: dict[str, object]`; plus `.as_log() -> dict`.
  - `resolve_kiosk_config(*, profile: KioskProfile | None, args: argparse.Namespace, overrides: dict[str, dict]) -> ResolvedKioskConfig`.
  - `check_firmware_compat(resolved: ResolvedKioskConfig, caps: FirmwareCaps) -> None`: raises `ValueError`.
  - `LOGGED_SETTINGS: tuple[str, ...]`: argparse dests recorded in `settings`.

Mode rules. Each refusal message names the flag to change.

| Inputs | Result |
|---|---|
| `explicit` given | Validated against the other flags (the rows below), then returned |
| No `--iwr6843`, or IWR without self-trigger, and no ST/SM | `SOUND_GATE` |
| Self-trigger and no ST/SM | `IWR_PRIMARY` |
| Self-trigger with ST/SM (either flag) | `OPS_INDEPENDENT` |
| ST/SM without self-trigger | Refuse: "ST/SM makes the OPS trigger itself; with the sound gate also on HOST_INT both fire. Use --trigger-mode ops_independent with --iwr6843-self-trigger, or drop ST/SM" |
| ST/SM with `trigger_type == "speed"` | Refuse (weawer `09216178`): "--ops-trigger-* only apply to the persisted rolling buffer; --trigger speed would ignore them" |
| `IWR_PRIMARY` without self-trigger | Refuse: "iwr_primary needs --iwr6843-self-trigger" |
| `IWR_PRIMARY` with ST/SM | Refuse: "iwr_primary sends S!; ST/SM would make the OPS trigger itself too (double trigger). Remove --ops-trigger-speed-mph/--ops-trigger-magnitude or use --trigger-mode ops_independent" |
| `OPS_INDEPENDENT` without ST/SM | Refuse: "ops_independent needs the OPS onboard trigger: set --ops-trigger-speed-mph (OmniPreSense tested -40) and --ops-trigger-magnitude (600)" |
| `IWR_PRIMARY`/`OPS_INDEPENDENT` with `trigger_type != "sound"` | Refuse: "use --trigger sound (the rolling-buffer reader)" |
| `SOUND_GATE` with self-trigger | Refuse: "sound_gate has no IWR self-trigger; drop --iwr6843-self-trigger or pick iwr_primary" |

Compatibility rules for `check_firmware_compat`:
- Unknown build with an experimental profile → refuse: "profile X is experimental and needs firmware that reports its capabilities (stats caps); flash a current build (firmware/iwr6843, make bin) or use --iwr6843-profile wide-3ms".
- Unknown build with a non-experimental profile or no profile → OK. The server logs `"[IWR6843] Firmware does not report capabilities (build unknown); cannot verify <formats> support"`.
- A known build that lacks the profile's capture format is refused, and the message names the fallback: `"... use --iwr6843-profile wide-3ms"` when the missing format is not iq16.
- Known build: `capture_format` must be in `caps.formats`; missing gives "flashed build B cannot capture FORMAT (has ...)".
- Known build: `requires_features ⊆ caps.features`; missing gives "flashed build B lacks features: a, b (profile X)".
- `trigger_mode` in {`IWR_PRIMARY`, `OPS_INDEPENDENT`} additionally needs `{"trigger", "release"}`.

- [ ] **Step 1: Port the 2 ms cfg and create the profiles**

Port weawer's config verbatim, then replace **only** its leading `%` comment block with a header that records where it came from:

```bash
git show weawer/feat/iwr-2ms-trigger-merge:config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg > config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg
```

The new header, above `dfeDataOutputMode 1` (every non-comment line stays byte-identical):

```
% Default kiosk capture (2026-09-27): 36 frames at 2 ms, all 12 loops, 3 TX / 4 RX,
% adaptive16. Ported verbatim from weawer/feat/iwr-2ms-trigger-merge (71a7f67).
% Pre/impact window origins are shifted +2 bins for the measured range bias
% (reflector at 1.20 m and 1.845 m centroided +1.5..1.8 bins), consistent with
% the biased tee bin the host now arms (Calibration.apparent_range).
% Not yet qualified by the stage 6 hardware session. There is no captureCfg retain
% line, so it runs on the firmware's default retention. Fallback: --iwr6843-profile wide-3ms.
```

Add a test to `tests/test_iwr6843_monitor.py`. It pins the capture lines, so a later edit to this file is deliberate:

```python
def test_default_2ms_cfg_capture_lines_match_the_ported_profile():
    lines = [
        line.strip()
        for line in Path("config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg").read_text().splitlines()
        if line.strip() and not line.startswith("%")
    ]
    assert "frameCfg 0 2 12 0 2 1 0" in lines
    assert "captureFormat adaptive16" in lines
    assert "phaseCaptureCfg 22 32 14 34 53 6 47 12 47 16 1" in lines
    assert not any(line.startswith("captureCfg retain") for line in lines)


def test_default_2ms_cfg_covers_the_biased_default_tee():
    cfg = "config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg"
    cal = Calibration.load("config/iwr6843_calibration_reference.json")
    tee = tee_global_bin(1.575, cfg, range_bias_m=cal.range_bias_m)
    assert tee == 35
    assert trigger_watch_bins(tee, cfg) == (23, 38)  # window 22-53
```

`config/kiosk/iq16-2ms.json` (the default):

```json
{
  "name": "iq16-2ms",
  "description": "Default kiosk: 36-frame 2 ms adaptive16 capture (weawer 71a7f67), IWR self-trigger relays S! to the OPS, ball detector on, onboard tracking on. Not yet stage-6 qualified; fall back with --iwr6843-profile wide-3ms.",
  "experimental": false,
  "requires": {"formats": ["adaptive16"], "features": ["trigger", "release"]},
  "args": {
    "iwr6843": true,
    "iwr6843_config": "config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg",
    "iwr6843_cal": "config/iwr6843_calibration_reference.json",
    "iwr6843_tee_m": 1.575,
    "iwr6843_self_trigger": true,
    "trigger_mode": "iwr_primary",
    "iwr6843_ball_detector": "on",
    "iwr6843_onboard_track": true,
    "iwr6843_onboard_metrics": false,
    "iwr6843_dump_stall_tolerance_s": 8.0
  }
}
```

`config/kiosk/wide-3ms.json` (previous working profile, kept for comparison and recovery):

```json
{
  "name": "wide-3ms",
  "description": "Previous working kiosk: wide 24-frame 3 ms IQ16 capture, IWR self-trigger relays S! to the OPS, ball detector on, onboard tracking on.",
  "experimental": false,
  "requires": {"formats": ["iq16"], "features": ["trigger", "release"]},
  "args": {
    "iwr6843": true,
    "iwr6843_config": "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",
    "iwr6843_cal": "config/iwr6843_calibration_reference.json",
    "iwr6843_tee_m": 1.575,
    "iwr6843_self_trigger": true,
    "trigger_mode": "iwr_primary",
    "iwr6843_ball_detector": "on",
    "iwr6843_onboard_track": true,
    "iwr6843_onboard_metrics": false,
    "iwr6843_dump_stall_tolerance_s": 8.0
  }
}
```

`DEFAULT_PROFILE = "iq16-2ms"` lives in `kiosk_config.py`. It is the profile the docs name as the default. The server's `--iwr6843-config` default moves to the same cfg (Task 5), so a bare `--iwr6843` run and `--iwr6843-profile iq16-2ms` capture the same way.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_kiosk_config.py
"""Profile loading, trigger-mode resolution and firmware compatibility are pure and table-tested."""

import argparse
import json

import pytest

from openflight.iwr6843.caps import FirmwareCaps
from openflight.kiosk_config import (
    KioskProfile,
    TriggerMode,
    check_firmware_compat,
    load_profile,
    profile_arg_errors,
    resolve_kiosk_config,
    resolve_trigger_mode,
)
from openflight.kiosk_config import DEFAULT_PROFILE


def _mode(**overrides):
    kwargs = {
        "explicit": None,
        "iwr6843": True,
        "self_trigger": True,
        "ops_trigger_speed_mph": None,
        "ops_trigger_magnitude": None,
        "trigger_type": "sound",
    }
    kwargs.update(overrides)
    return resolve_trigger_mode(**kwargs)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"iwr6843": False, "self_trigger": False}, TriggerMode.SOUND_GATE),
        ({"self_trigger": False}, TriggerMode.SOUND_GATE),
        ({}, TriggerMode.IWR_PRIMARY),
        ({"ops_trigger_speed_mph": -40.0, "ops_trigger_magnitude": 600}, TriggerMode.OPS_INDEPENDENT),
        ({"ops_trigger_speed_mph": -40.0}, TriggerMode.OPS_INDEPENDENT),
        ({"explicit": "iwr_primary"}, TriggerMode.IWR_PRIMARY),
        ({"explicit": "ops_independent", "ops_trigger_magnitude": 600}, TriggerMode.OPS_INDEPENDENT),
        ({"explicit": "sound_gate", "self_trigger": False}, TriggerMode.SOUND_GATE),
    ],
)
def test_trigger_mode_resolution(overrides, expected):
    assert _mode(**overrides) is expected


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"self_trigger": False, "ops_trigger_speed_mph": -40.0}, "sound gate"),
        ({"trigger_type": "speed", "ops_trigger_speed_mph": -40.0}, "--trigger speed"),
        ({"explicit": "iwr_primary", "self_trigger": False}, "--iwr6843-self-trigger"),
        ({"explicit": "iwr_primary", "ops_trigger_magnitude": 600}, "double trigger"),
        ({"explicit": "ops_independent"}, "--ops-trigger-speed-mph"),
        ({"trigger_type": "speed"}, "--trigger sound"),
        ({"explicit": "sound_gate"}, "drop --iwr6843-self-trigger"),
        ({"explicit": "bogus"}, "sound_gate, iwr_primary, ops_independent"),
    ],
)
def test_trigger_mode_refusals_are_actionable(overrides, message):
    with pytest.raises(ValueError, match=message):
        _mode(**overrides)


def test_load_named_profiles_shipped_in_repo():
    default = load_profile("iq16-2ms")
    assert not default.experimental and default.args["trigger_mode"] == "iwr_primary"
    assert default.requires_formats == {"adaptive16"}
    fallback = load_profile("wide-3ms")
    assert fallback.args["iwr6843_config"].endswith("wide_24f3ms_53bin_iq16.cfg")
    assert DEFAULT_PROFILE == "iq16-2ms"


def test_load_profile_by_path(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"name": "p", "description": "", "experimental": False,
                                "requires": {"formats": [], "features": []}, "args": {}}))
    assert load_profile(str(path)).source == str(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{not json", "not valid JSON"),
        (json.dumps({"name": "p"}), "missing keys"),
        (json.dumps({"name": "p", "description": "", "experimental": "yes",
                     "requires": {"formats": [], "features": []}, "args": {}}), "experimental"),
    ],
)
def test_malformed_profiles_are_refused(tmp_path, content, message):
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(ValueError, match=message):
        load_profile(str(path))


def test_unknown_profile_name_lists_the_available_ones():
    with pytest.raises(ValueError, match="iq16-2ms, wide-3ms"):
        load_profile("nope")


def test_profile_args_must_be_real_cli_destinations():
    profile = load_profile("iq16-2ms")
    bad = KioskProfile(**{**profile.__dict__, "args": {**profile.args, "iwr6843_tee": 1.5}})
    assert profile_arg_errors(bad, {"iwr6843", "iwr6843_config"}) != []
    assert "iwr6843_tee" in " ".join(profile_arg_errors(bad, set(profile.args)))


def _args(**values):
    base = {
        "iwr6843": True, "iwr6843_config": "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",
        "iwr6843_cal": "config/iwr6843_calibration_reference.json", "iwr6843_tee_m": 1.575,
        "iwr6843_self_trigger": True, "iwr6843_self_trigger_bin": None, "trigger_mode": None,
        "ops_trigger_speed_mph": None, "ops_trigger_magnitude": None, "trigger": "sound",
        "iwr6843_ball_detector": "on", "iwr6843_onboard_track": True,
        "iwr6843_onboard_metrics": False, "iwr6843_dump_stall_tolerance_s": 8.0,
    }
    base.update(values)
    return argparse.Namespace(**base)


def test_resolved_config_records_profile_overrides_and_format():
    resolved = resolve_kiosk_config(
        profile=load_profile("iq16-2ms"),
        args=_args(iwr6843_config="config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg", iwr6843_tee_m=1.6),
        overrides={"iwr6843_tee_m": {"profile": 1.575, "cli": 1.6}},
    )
    log = resolved.as_log()
    assert log["profile"] == "iq16-2ms" and log["trigger_mode"] == "iwr_primary"
    assert log["capture_format"] == "adaptive16"
    assert log["overrides"] == {"iwr6843_tee_m": {"profile": 1.575, "cli": 1.6}}
    assert log["settings"]["iwr6843_tee_m"] == 1.6


def test_no_profile_still_resolves_from_flags():
    resolved = resolve_kiosk_config(profile=None, args=_args(), overrides={})
    assert resolved.profile is None and resolved.trigger_mode is TriggerMode.IWR_PRIMARY


_CFG = {
    "iq16-2ms": "config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg",
    "wide-3ms": "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",
}


def _caps(formats=("iq16", "adaptive16"), features=("trigger", "release", "track")):
    return FirmwareCaps(True, "b123", frozenset(formats), frozenset(features))


def _resolved(profile_name="iq16-2ms", **arg_values):
    arg_values.setdefault("iwr6843_config", _CFG.get(profile_name, _CFG["iq16-2ms"]))
    return resolve_kiosk_config(
        profile=load_profile(profile_name), args=_args(**arg_values), overrides={}
    )


def _experimental_profile(tmp_path):
    path = tmp_path / "exp.json"
    path.write_text(json.dumps({
        "name": "exp", "description": "", "experimental": True,
        "requires": {"formats": ["iq16"], "features": ["track"]}, "args": {},
    }))
    return load_profile(str(path))


@pytest.mark.parametrize(
    ("resolved_kwargs", "caps", "message"),
    [
        ({}, _caps(formats=("iq16",)), "cannot capture adaptive16.*wide-3ms"),
        ({"profile_name": "wide-3ms"}, _caps(formats=("iq8",)), "cannot capture iq16"),
        ({}, _caps(features=("trigger",)), "release"),
    ],
)
def test_firmware_compat_refusals(resolved_kwargs, caps, message):
    with pytest.raises(ValueError, match=message):
        check_firmware_compat(_resolved(**resolved_kwargs), caps)


def test_experimental_profile_refused_on_unknown_firmware(tmp_path):
    resolved = resolve_kiosk_config(
        profile=_experimental_profile(tmp_path), args=_args(), overrides={}
    )
    with pytest.raises(ValueError, match="stats caps"):
        check_firmware_compat(resolved, FirmwareCaps.unknown())


def test_experimental_profile_needs_its_features_on_known_firmware(tmp_path):
    resolved = resolve_kiosk_config(
        profile=_experimental_profile(tmp_path), args=_args(), overrides={}
    )
    with pytest.raises(ValueError, match="track"):
        check_firmware_compat(resolved, _caps(features=("trigger", "release")))


@pytest.mark.parametrize("profile_name", ["iq16-2ms", "wide-3ms"])
def test_unknown_firmware_may_run_the_shipped_profiles(profile_name):
    check_firmware_compat(_resolved(profile_name), FirmwareCaps.unknown())


def test_known_firmware_with_everything_passes():
    check_firmware_compat(_resolved(), _caps())
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_kiosk_config.py -v`
Expected: FAIL (`No module named openflight.kiosk_config`).

- [ ] **Step 4: Implement `src/openflight/kiosk_config.py`**

```python
"""Kiosk profiles, trigger ownership and firmware compatibility, resolved once at startup.

A profile names the capture cfg, calibration, tee, trigger mode and onboard
features that belong together. Explicit CLI flags still win and are recorded
as overrides, so a session log says exactly what ran. Everything here is pure:
the server feeds it parsed args and the firmware's ``stats caps`` reply.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from openflight.iwr6843.caps import FirmwareCaps

DEFAULT_PROFILES_DIR = Path("config/kiosk")
DEFAULT_PROFILE = "iq16-2ms"  # 2 ms adaptive16 capture (user decision 2026-09-27)
FALLBACK_PROFILE = "wide-3ms"  # previous working capture, for comparison and recovery

# argparse dests written into session_start["kiosk"]["settings"].
LOGGED_SETTINGS = (
    "iwr6843",
    "iwr6843_config",
    "iwr6843_cal",
    "iwr6843_tee_m",
    "iwr6843_self_trigger",
    "iwr6843_self_trigger_bin",
    "iwr6843_ball_detector",
    "iwr6843_onboard_track",
    "iwr6843_onboard_metrics",
    "iwr6843_dump_stall_tolerance_s",
    "ops_trigger_speed_mph",
    "ops_trigger_magnitude",
    "trigger",
)

_RELAY_FEATURES = frozenset({"trigger", "release"})


class TriggerMode(str, Enum):
    """Which device owns the shot trigger."""

    SOUND_GATE = "sound_gate"  # SEN-14262 edge on OPS HOST_INT (and IWR GPIO)
    IWR_PRIMARY = "iwr_primary"  # IWR freezes, notifies camera, sends OPS S!
    OPS_INDEPENDENT = "ops_independent"  # OPS ST/SM triggers itself; IWR never sends S!


@dataclass(frozen=True)
class KioskProfile:
    name: str
    description: str
    experimental: bool
    requires_formats: frozenset[str]
    requires_features: frozenset[str]
    args: dict[str, object]
    source: str


def _profile_path(name_or_path: str, profiles_dir: Path) -> Path:
    candidate = Path(name_or_path)
    if candidate.suffix == ".json" or candidate.exists():
        return candidate
    path = profiles_dir / f"{name_or_path}.json"
    if not path.is_file():
        available = sorted(p.stem for p in profiles_dir.glob("*.json"))
        raise ValueError(
            f"unknown kiosk profile {name_or_path!r}; available: {', '.join(available) or 'none'}"
        )
    return path


def load_profile(name_or_path: str, profiles_dir: Path = DEFAULT_PROFILES_DIR) -> KioskProfile:
    """Load a named profile from ``config/kiosk`` or a JSON file path."""
    path = _profile_path(name_or_path, profiles_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"kiosk profile {path} is not valid JSON: {error}") from error
    missing = {"name", "description", "experimental", "requires", "args"} - set(raw)
    if missing:
        raise ValueError(f"kiosk profile {path} is missing keys: {', '.join(sorted(missing))}")
    if not isinstance(raw["experimental"], bool):
        raise ValueError(f"kiosk profile {path}: 'experimental' must be true or false")
    if not isinstance(raw["args"], dict):
        raise ValueError(f"kiosk profile {path}: 'args' must be an object")
    requires = raw["requires"] or {}
    return KioskProfile(
        name=str(raw["name"]),
        description=str(raw["description"]),
        experimental=raw["experimental"],
        requires_formats=frozenset(requires.get("formats", ())),
        requires_features=frozenset(requires.get("features", ())),
        args=dict(raw["args"]),
        source=str(path),
    )


def profile_arg_errors(profile: KioskProfile, known_dests: set[str]) -> list[str]:
    """Profile ``args`` keys that are not CLI destinations (typos would silently do nothing)."""
    return [
        f"kiosk profile {profile.name}: unknown setting {key!r}"
        for key in sorted(profile.args)
        if key not in known_dests
    ]


def resolve_trigger_mode(
    *,
    explicit: str | None,
    iwr6843: bool,
    self_trigger: bool,
    ops_trigger_speed_mph: float | None,
    ops_trigger_magnitude: int | None,
    trigger_type: str,
) -> TriggerMode:
    """One trigger owner, from the explicit mode or inferred from the flags."""
    ops_onboard = ops_trigger_speed_mph is not None or ops_trigger_magnitude is not None
    if ops_onboard and trigger_type == "speed":
        raise ValueError(
            "--ops-trigger-speed-mph/--ops-trigger-magnitude only apply to the persisted "
            "rolling buffer; --trigger speed would ignore them"
        )
    if explicit is None:
        if not (iwr6843 and self_trigger):
            mode = TriggerMode.SOUND_GATE
        elif ops_onboard:
            mode = TriggerMode.OPS_INDEPENDENT
        else:
            mode = TriggerMode.IWR_PRIMARY
    else:
        try:
            mode = TriggerMode(explicit)
        except ValueError as error:
            choices = ", ".join(m.value for m in TriggerMode)
            raise ValueError(f"--trigger-mode must be one of {choices}") from error

    if mode is TriggerMode.SOUND_GATE:
        if self_trigger:
            raise ValueError(
                "sound_gate has no IWR self-trigger; drop --iwr6843-self-trigger or pick "
                "--trigger-mode iwr_primary"
            )
        if ops_onboard:
            raise ValueError(
                "ST/SM makes the OPS trigger itself; with the sound gate also on HOST_INT both "
                "fire. Use --trigger-mode ops_independent with --iwr6843-self-trigger, or drop "
                "--ops-trigger-speed-mph/--ops-trigger-magnitude"
            )
        return mode
    if not (iwr6843 and self_trigger):
        raise ValueError(f"{mode.value} needs --iwr6843 --iwr6843-self-trigger")
    if trigger_type != "sound":
        raise ValueError(f"{mode.value} reads the OPS rolling buffer; use --trigger sound")
    if mode is TriggerMode.IWR_PRIMARY and ops_onboard:
        raise ValueError(
            "iwr_primary sends S!; ST/SM would make the OPS trigger itself too (double trigger). "
            "Remove --ops-trigger-speed-mph/--ops-trigger-magnitude or use --trigger-mode "
            "ops_independent"
        )
    if mode is TriggerMode.OPS_INDEPENDENT and not ops_onboard:
        raise ValueError(
            "ops_independent needs the OPS onboard trigger: set --ops-trigger-speed-mph "
            "(OmniPreSense tested -40) and --ops-trigger-magnitude (600)"
        )
    return mode


@dataclass(frozen=True)
class ResolvedKioskConfig:
    profile: str | None
    profile_source: str | None
    experimental: bool
    trigger_mode: TriggerMode
    overrides: dict[str, dict]
    requires_formats: frozenset[str]
    requires_features: frozenset[str]
    capture_format: str | None
    settings: dict[str, object] = field(default_factory=dict)

    def as_log(self) -> dict:
        return {
            "profile": self.profile,
            "profile_source": self.profile_source,
            "experimental": self.experimental,
            "trigger_mode": self.trigger_mode.value,
            "overrides": self.overrides,
            "requires_formats": sorted(self.requires_formats),
            "requires_features": sorted(self.requires_features),
            "capture_format": self.capture_format,
            "settings": self.settings,
        }


def resolve_kiosk_config(
    *, profile: KioskProfile | None, args: argparse.Namespace, overrides: dict[str, dict]
) -> ResolvedKioskConfig:
    """Validate trigger ownership and gather what the session log must record."""
    from openflight.iwr6843.monitor import (  # pylint: disable=import-outside-toplevel
        read_capture_config,
    )

    mode = resolve_trigger_mode(
        explicit=getattr(args, "trigger_mode", None),
        iwr6843=bool(getattr(args, "iwr6843", False)),
        self_trigger=bool(getattr(args, "iwr6843_self_trigger", False)),
        ops_trigger_speed_mph=getattr(args, "ops_trigger_speed_mph", None),
        ops_trigger_magnitude=getattr(args, "ops_trigger_magnitude", None),
        trigger_type=getattr(args, "trigger", "sound"),
    )
    capture_format = None
    if getattr(args, "iwr6843", False):
        capture_format = read_capture_config(args.iwr6843_config).capture_format or "iq16"
    return ResolvedKioskConfig(
        profile=profile.name if profile else None,
        profile_source=profile.source if profile else None,
        experimental=profile.experimental if profile else False,
        trigger_mode=mode,
        overrides=dict(overrides),
        requires_formats=profile.requires_formats if profile else frozenset(),
        requires_features=profile.requires_features if profile else frozenset(),
        capture_format=capture_format,
        settings={key: getattr(args, key, None) for key in LOGGED_SETTINGS},
    )


def check_firmware_compat(resolved: ResolvedKioskConfig, caps: FirmwareCaps) -> None:
    """Refuse a configuration the flashed firmware cannot run."""
    name = resolved.profile or "(no profile)"
    if not caps.known:
        if resolved.experimental:
            raise ValueError(
                f"profile {name} is experimental and needs firmware that reports its "
                "capabilities (stats caps); flash a current build (firmware/iwr6843, make bin) "
                f"or use --iwr6843-profile {FALLBACK_PROFILE}"
            )
        return
    formats = set(resolved.requires_formats)
    if resolved.capture_format:
        formats.add(resolved.capture_format)
    missing_formats = sorted(formats - caps.formats)
    if missing_formats:
        fallback = (
            f"; use --iwr6843-profile {FALLBACK_PROFILE}"
            if "iq16" in caps.formats and "iq16" not in missing_formats
            else ""
        )
        raise ValueError(
            f"flashed build {caps.build} cannot capture {', '.join(missing_formats)} "
            f"(has {', '.join(sorted(caps.formats)) or 'none'}; profile {name}){fallback}"
        )
    needed = set(resolved.requires_features)
    if resolved.trigger_mode is not TriggerMode.SOUND_GATE:
        needed |= _RELAY_FEATURES
    missing = sorted(needed - caps.features)
    if missing:
        raise ValueError(
            f"flashed build {caps.build} lacks features: {', '.join(missing)} (profile {name})"
        )
```

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest tests/test_kiosk_config.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/openflight/kiosk_config.py config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg config/kiosk/iq16-2ms.json config/kiosk/wide-3ms.json tests/test_kiosk_config.py tests/test_iwr6843_monitor.py
git commit -m "kiosk: 2 ms adaptive16 default profile (ported), wide-3ms fallback, trigger modes, firmware compatibility"
```

---

### Task 5: Server wiring for profiles, ST/SM flags, caps check and session logging (decisions 1A, 2A, 15A)

**Files:**
- Modify: `src/openflight/server.py`:
  - `main()`: argument definitions around `:4798` and `:4877-5050`
  - validation at `:5160-5176`
  - `init_iwr6843` at `:1154` and its call at `:5345`
  - `_session_start_config` at `:913`
  - IWR `log_connection` at `:4161`
  - `start_monitor` signature at `:~3891`
- Modify: `src/openflight/iwr6843/monitor.py` (`start()` at `:334`: new `caps_check` parameter, `firmware_caps` attribute)
- Modify: `src/openflight/rolling_buffer/monitor.py` (`__init__` / `connect`: ST/SM pass-through)
- Modify: `src/openflight/ops243.py` (`prepare_persisted_rolling_buffer` at `:~1353`: ST/SM, ported from weawer `c866ff99`)
- Modify: `scripts/start-kiosk.sh` (governor warning)
- Create: `scripts/hardware-test/set_cpu_governor.sh` (port from `git show weawer/feat/iwr-2ms-trigger-merge:scripts/hardware-test/set_cpu_governor.sh`)
- Test: `tests/test_server.py`, `tests/test_iwr6843_monitor.py`, `tests/test_ops243_mode_commands.py`, `tests/test_rolling_buffer.py`, `tests/test_start_kiosk.py`

**Interfaces:**
- Consumes: Task 4 (`load_profile`, `profile_arg_errors`, `resolve_kiosk_config`, `check_firmware_compat`, `TriggerMode`), Task 3 (`FirmwareCaps`, `IWR6843Radar.capabilities`).
- Produces:
  - `server.parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, ResolvedKioskConfig]`: extracted from `main()` so it can be tested.
  - Module globals `kiosk_config: ResolvedKioskConfig | None` and `trigger_mode: TriggerMode`.
  - `IWR6843CaptureMonitor.start(*, armed, onboard_track_config, caps_check: Callable[[FirmwareCaps], None] | None = None)`.
  - `IWR6843CaptureMonitor.firmware_caps: FirmwareCaps`.
  - `OPS243Radar.prepare_persisted_rolling_buffer(..., trigger_speed_mph: float | None = None, trigger_magnitude: int | None = None)`.
  - `RollingBufferMonitor(..., trigger_speed_mph=None, trigger_magnitude=None)`.
  - `server._cpu_governor() -> str | None`.
  - New CLI flags:
    - `--iwr6843-profile NAME|PATH`
    - `--trigger-mode {sound_gate,iwr_primary,ops_independent}` (default `None`, meaning inferred)
    - `--ops-trigger-speed-mph FLOAT`
    - `--ops-trigger-magnitude INT`
    - `--iwr6843-dump-stall-tolerance-s FLOAT` (default 8.0)

- [ ] **Step 1: Write the failing tests**

In `tests/test_server.py`:

```python
def test_profile_supplies_defaults_and_cli_overrides_are_recorded():
    args, resolved = server.parse_args(["--iwr6843-profile", "wide-3ms", "--iwr6843-tee-m", "1.6"])
    assert args.iwr6843 and args.iwr6843_self_trigger
    assert args.iwr6843_config.endswith("wide_24f3ms_53bin_iq16.cfg")
    assert resolved.profile == "wide-3ms"
    assert resolved.overrides == {"iwr6843_tee_m": {"profile": 1.575, "cli": 1.6}}


def test_profile_with_unknown_setting_is_refused(tmp_path, capsys):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"name": "p", "description": "", "experimental": False,
                                "requires": {"formats": [], "features": []},
                                "args": {"iwr6843_tee": 1.5}}))
    with pytest.raises(SystemExit):
        server.parse_args(["--iwr6843-profile", str(path)])
    assert "unknown setting 'iwr6843_tee'" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--iwr6843", "--iwr6843-self-trigger", "--trigger-mode", "iwr_primary",
          "--ops-trigger-magnitude", "600"], "double trigger"),
        (["--iwr6843", "--iwr6843-self-trigger", "--trigger-mode", "ops_independent"],
         "--ops-trigger-speed-mph"),
        (["--ops-trigger-speed-mph", "-40"], "sound gate"),
    ],
)
def test_cli_refuses_conflicting_trigger_owners(argv, message, capsys):
    with pytest.raises(SystemExit):
        server.parse_args(argv)
    assert message in capsys.readouterr().err


def test_no_profile_defaults_to_the_2ms_capture_cfg():
    args, resolved = server.parse_args([])
    assert resolved.profile is None and resolved.trigger_mode.value == "sound_gate"
    assert args.iwr6843_config == "config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg"


def test_default_profile_and_bare_flags_capture_the_same_way():
    bare, _ = server.parse_args(["--iwr6843"])
    profiled, resolved = server.parse_args(["--iwr6843-profile", "iq16-2ms"])
    assert bare.iwr6843_config == profiled.iwr6843_config
    assert resolved.capture_format == "adaptive16"


def test_unknown_firmware_warns_that_formats_are_unverified(caplog):
    ...  # init_iwr6843 with a fake capture monitor whose firmware_caps is FirmwareCaps.unknown()
         # -> one WARNING containing "build unknown" and "adaptive16"


def test_session_start_config_records_kiosk_caps_bias_and_governor(monkeypatch):
    _, resolved = server.parse_args(["--iwr6843-profile", "iq16-2ms"])
    monkeypatch.setattr(server, "kiosk_config", resolved)
    monkeypatch.setattr(server, "iwr6843_runtime_config", {
        "enabled": True, "range_bias_m": 0.066,
        "firmware": {"known": True, "build": "b", "formats": ["iq16"], "features": ["trigger"]},
    })
    monkeypatch.setattr(server, "_cpu_governor", lambda: "ondemand")
    config = server._session_start_config()
    assert config["kiosk"]["profile"] == "iq16-2ms"
    assert config["iwr6843"]["firmware"]["build"] == "b"
    assert config["iwr6843"]["range_bias_m"] == 0.066
    assert config["host"]["cpu_governor"] == "ondemand"
```

In `tests/test_iwr6843_monitor.py`, using that file's existing fake radar/monitor builder:

```python
def test_start_queries_caps_before_config_and_refuses_incompatible_firmware():
    radar = _fake_radar()  # existing helper
    radar.capabilities = lambda: FirmwareCaps.unknown()
    monitor = _monitor(radar=radar)

    def refuse(caps):
        raise ValueError("flash firmware")

    with pytest.raises(ValueError, match="flash firmware"):
        monitor.start(armed=False, caps_check=refuse)
    assert radar.sent_configs == []  # nothing configured on refused firmware


def test_start_records_firmware_caps():
    radar = _fake_radar()
    caps = FirmwareCaps(True, "b", frozenset({"iq16"}), frozenset({"trigger"}))
    radar.capabilities = lambda: caps
    monitor = _monitor(radar=radar)
    monitor.start(armed=False)
    assert monitor.firmware_caps == caps
```

If the file's fake radar has no `capabilities` attribute, add a default `capabilities()` returning `FirmwareCaps.unknown()` to `tests/iwr6843_fakes.py`, so every existing test keeps passing. If the fake records configs under a name other than `sent_configs`, use that name.

In `tests/test_ops243_mode_commands.py` (port of weawer `c866ff99`):

```python
def test_prepare_persisted_rolling_buffer_sends_st_sm_only_when_given():
    radar, port = _radar_with_recording_serial()  # existing helper style in this file
    radar.prepare_persisted_rolling_buffer(trigger_speed_mph=-40.0, trigger_magnitude=600)
    sent = b"".join(port.writes)
    assert b"ST-40" in sent and b"SM600" in sent
    assert sent.index(b"ST-40") < sent.rindex(b"PA")


def test_prepare_persisted_rolling_buffer_default_sends_no_st_sm():
    radar, port = _radar_with_recording_serial()
    radar.prepare_persisted_rolling_buffer()
    sent = b"".join(port.writes)
    assert b"ST" not in sent.replace(b"STR", b"") and b"SM" not in sent


def test_speed_trigger_mode_never_sends_st_sm():
    radar, port = _radar_with_recording_serial()
    radar.configure_for_speed_trigger()
    sent = b"".join(port.writes)
    assert b"ST-" not in sent and b"SM6" not in sent
```

Before writing these, check how the file captures writes (`grep -n "def _\|class " tests/test_ops243_mode_commands.py`) and use the same helper. Replace the `_radar_with_recording_serial` name with that helper.

In `tests/test_rolling_buffer.py`: `RollingBufferMonitor(trigger_type="sound", trigger_speed_mph=-40.0, trigger_magnitude=600)` passes both values to `prepare_persisted_rolling_buffer` on `connect()`. Use the file's existing fake radar approach for `connect()`.

In `tests/test_start_kiosk.py`, following that file's pattern for asserting server args:
- `--iwr6843-profile iq16-2ms` passes through to the server command.
- The script prints `CPU governor` when the governor file reports `ondemand`. The script must read the path from `OPENFLIGHT_CPU_GOVERNOR_FILE` when set, so the test can point it at a temp file.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_server.py -k "profile or trigger_owners or session_start_config_records" tests/test_iwr6843_monitor.py -k caps tests/test_ops243_mode_commands.py tests/test_rolling_buffer.py -k "st_sm or trigger_speed" tests/test_start_kiosk.py -v`
Expected: FAIL (`parse_args` missing, unknown flags, and so on).

- [ ] **Step 3: Implement**

1. **`server.parse_args(argv)`.** Move the `parser = argparse.ArgumentParser(...)` construction, all `add_argument` calls and all validation that currently lives in `main()` before hardware init (through the `self_trigger_config` checks at `:5176`, plus the camera checks) into `parse_args`. `main()` calls `args, resolved = parse_args()`, stores `resolved` in the module global `kiosk_config` and `resolved.trigger_mode` in `trigger_mode`, and keeps `self_trigger_config` by recomputing `_self_trigger_config(args)` or by returning it too. Choose one: return `(args, resolved, self_trigger_config)` and update the interface above if you do. Keep behaviour identical for existing flags.
2. **Two-pass profile parse**, inside `parse_args`:

```python
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--iwr6843-profile")
    known, _ = pre.parse_known_args(argv)
    profile = None
    if known.iwr6843_profile:
        try:
            profile = load_profile(known.iwr6843_profile)
        except ValueError as error:
            parser.error(str(error))
        dests = {action.dest for action in parser._actions}  # pylint: disable=protected-access
        errors = profile_arg_errors(profile, dests)
        if errors:
            parser.error("; ".join(errors))
        parser.set_defaults(**profile.args)
    args = parser.parse_args(argv)
    overrides = (
        {
            key: {"profile": value, "cli": getattr(args, key)}
            for key, value in profile.args.items()
            if getattr(args, key) != value
        }
        if profile
        else {}
    )
```

   Replace the old `if self_trigger_config is not None and args.trigger != "sound"` check with:

```python
    try:
        resolved = resolve_kiosk_config(profile=profile, args=args, overrides=overrides)
    except (OSError, ValueError) as error:
        parser.error(str(error))
```

3. **Default cfg**: change `--iwr6843-config`'s default (`server.py:~4896`) to `"config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg"`, with the comment "Default since 2026-09-27: 2 ms adaptive16 (kiosk profile iq16-2ms). Fallback: --iwr6843-profile wide-3ms." Existing tests that relied on the wide default must now pass `--iwr6843-config config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg` explicitly. Don't loosen their assertions. Their tee bins are computed for the wide window (20–72); the 2 ms pre-window is 22–53.
4. **New flags**: `--iwr6843-profile`, `--trigger-mode` (`choices=[m.value for m in TriggerMode]`, default `None`), `--ops-trigger-speed-mph` (`type=float`), `--ops-trigger-magnitude` (`type=int`) and `--iwr6843-dump-stall-tolerance-s` (`type=float`, default `8.0`). Copy the help texts from weawer `c866ff99` for the two ST/SM flags, adding "only with --trigger-mode ops_independent".
5. **`init_iwr6843(..., caps_check=None, dump_stall_tolerance_s=8.0)`.**
   - Pass `caps_check` to `capture_monitor.start(...)`.
   - After start, add `"firmware": capture_monitor.firmware_caps.as_log()`, `"range_bias_m": calibration.range_bias_m` and `"dump_stall_tolerance_s": dump_stall_tolerance_s` to `iwr6843_runtime_config`.
   - When `not caps.known`, log `logger.warning("[IWR6843] Firmware does not report capabilities (build unknown); cannot verify %s support. Flash a current build to record it", ", ".join(sorted(needed_formats)))`, where `needed_formats` is the resolved capture format plus the profile's `requires_formats`.
   - `dump_stall_tolerance_s` is stored for Task 9, which threads it into the monitor.
   - In `main()`, call it with `caps_check=lambda caps: check_firmware_compat(kiosk_config, caps)`.
   - A `ValueError` from the check lands in `init_iwr6843`'s existing `except`, which records `iwr6843_runtime_config["error"]`. The existing `startup_status.error(... _iwr6843_startup_recovery(...))` path then shows the actionable message. Check that `_iwr6843_startup_recovery` passes unknown messages through; add a test if it rewrites them.
6. **`IWR6843CaptureMonitor.start`**: first line after the `config_path.is_file()` check:

```python
        # Before any configuration: a profile this firmware cannot run must
        # not half-configure the board.
        self.firmware_caps = self.radar.capabilities()
        if caps_check is not None:
            caps_check(self.firmware_caps)
```

   Also initialise `self.firmware_caps = FirmwareCaps.unknown()` in `__init__`.
7. **`log_connection`** for the IWR (`:4161`): `firmware=iwr6843_runtime_config.get("firmware", {}).get("build", "unknown")`.
8. **`_session_start_config()`**: add `"kiosk": kiosk_config.as_log() if kiosk_config else None` and `"host": {"cpu_governor": _cpu_governor()}`, where:

```python
_CPU_GOVERNOR_PATH = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")


def _cpu_governor() -> str | None:
    """The CPU frequency governor (trigger latency jitter depends on it), or None off-Pi."""
    try:
        return _CPU_GOVERNOR_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
```

9. **ST/SM.** Port weawer `c866ff99`'s changes to `prepare_persisted_rolling_buffer`, `RollingBufferMonitor.__init__`/`connect` and `start_monitor(..., ops_trigger_speed_mph=None, ops_trigger_magnitude=None)`, then pass `args.ops_trigger_speed_mph` and `args.ops_trigger_magnitude` from `main()`. Keep weawer's docstrings (`git show c866ff99 -- src/`).
10. **`start-kiosk.sh`**: before launching the server, add:

```bash
governor_file="${OPENFLIGHT_CPU_GOVERNOR_FILE:-/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor}"
if [[ -r "$governor_file" ]]; then
    governor="$(cat "$governor_file")"
    if [[ "$governor" != "performance" ]]; then
        echo "WARNING: CPU governor is '$governor'; trigger latency jitters with frequency scaling." >&2
        echo "         For latency runs: scripts/hardware-test/set_cpu_governor.sh (restore after)." >&2
    fi
fi
```

11. **`set_cpu_governor.sh`**: `git show weawer/feat/iwr-2ms-trigger-merge:scripts/hardware-test/set_cpu_governor.sh > scripts/hardware-test/set_cpu_governor.sh`, then read it end to end before committing, and `chmod +x` it. It is a manual tool; nothing calls it.

- [ ] **Step 4: Run to verify, plus the full server and kiosk suites**

Run: `uv run pytest tests/test_server.py tests/test_start_kiosk.py tests/test_iwr6843_monitor.py tests/test_ops243_mode_commands.py tests/test_rolling_buffer.py tests/test_kiosk_config.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/server.py src/openflight/iwr6843/monitor.py src/openflight/rolling_buffer/monitor.py src/openflight/ops243.py scripts/start-kiosk.sh scripts/hardware-test/set_cpu_governor.sh tests/test_server.py tests/test_iwr6843_monitor.py tests/test_ops243_mode_commands.py tests/test_rolling_buffer.py tests/test_start_kiosk.py tests/iwr6843_fakes.py
git commit -m "kiosk: --iwr6843-profile, explicit --trigger-mode, firmware caps check, resolved config in session_start"
```

---

### Task 6: One bounded-blocking OPS dump reader (decisions 5A, 13A, 8A)

**Files:**
- Modify: `src/openflight/ops243.py`: add `DumpStatus`, `DumpRead`, `_serial_timeout`, `_CompletionScanner` and `_read_iq_dump`; rewrite the read loops in `trigger_capture` (`:1391-1479`) and `wait_for_hardware_trigger` (`:1481-1603`); add clock class attributes
- Test: `tests/test_ops243_dump_reader.py` (new)

**Interfaces:**
- Consumes: `FakeClock`, `VirtualSerial` (Task 1).
- Produces:
  - `class DumpStatus(str, Enum)`: `COMPLETE`, `INCOMPLETE`, `CANCELLED`, `NO_TRIGGER`.
  - `@dataclass(frozen=True) class DumpRead`: `status: DumpStatus`, `text: str`, `bytes_received: int`, `elapsed_s: float`, `first_byte_wall: float | None`, `first_byte_mono: float | None`, `longest_gap_s: float`.
  - `OPS243Radar.last_dump_read: DumpRead | None`, set by both public methods.
  - `OPS243Radar._read_iq_dump(*, timeout_s: float, dump_grace_s: float, stall_tolerance_s: float, cancel_event, on_first_byte, require_marker: bool) -> DumpRead`.
  - Class attributes `_monotonic = staticmethod(time.monotonic)`, `_wall = staticmethod(time.time)`.
  - `OPS243Radar.DUMP_STALL_TOLERANCE_S = 2.0`.
  - `wait_for_hardware_trigger` and `trigger_capture` keep returning `str`: `DumpRead.text` for COMPLETE and INCOMPLETE, `""` otherwise.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ops243_dump_reader.py
"""The shared OPS dump reader: bounded reads, cancellation, fragments, stalls, linear scanning."""

import threading

import pytest

from openflight.ops243 import DumpStatus, OPS243Radar
from tests.serial_fakes import FakeClock, VirtualSerial

HEADER = b'{"sample_time":946.077}\r\n{"trigger_time":946.145}\r\n'
I_LINE = b'{"I":[2168,2187,2155,2154]}\r\n'
Q_LINE = b'{"Q":[2048,2050,2047,2049]}'
DUMP = HEADER + I_LINE + Q_LINE


def _radar(clock, port):
    radar = OPS243Radar.__new__(OPS243Radar)
    radar.serial = port
    radar.last_hardware_trigger_first_byte_timestamp = None
    radar.last_dump_read = None
    radar._monotonic = clock.monotonic
    radar._wall = clock.time
    radar.baud = 230_400
    return radar


def _setup(timeout=1.0):
    clock = FakeClock()
    port = VirtualSerial(clock, timeout=timeout)
    return clock, port, _radar(clock, port)


def test_complete_dump_in_one_chunk():
    clock, port, radar = _setup()
    port.feed_after(0.2, DUMP)
    text = radar.wait_for_hardware_trigger(timeout=5.0)
    assert text.startswith('{"sample_time"') and text.endswith("]}")
    assert radar.last_dump_read.status is DumpStatus.COMPLETE
    assert radar.last_dump_read.first_byte_mono == pytest.approx(1000.2)


@pytest.mark.parametrize("split", range(1, len(DUMP)))
def test_fragmented_at_every_offset_still_completes(split):
    clock, port, radar = _setup()
    port.feed_after(0.1, DUMP[:split])
    port.feed_after(0.15, DUMP[split:])
    radar.wait_for_hardware_trigger(timeout=5.0)
    assert radar.last_dump_read.status is DumpStatus.COMPLETE


@pytest.mark.parametrize("split", range(1, len(b'{"sample_time"')))
def test_marker_split_across_reads_after_idle_noise(split):
    clock, port, radar = _setup()
    port.feed_after(0.05, b"clock 12345\r\n" + DUMP[:split])
    port.feed_after(0.10, DUMP[split:])
    text = radar.wait_for_hardware_trigger(timeout=5.0)
    assert text.startswith('{"sample_time"')


def test_first_byte_timestamp_is_not_quantised_by_polling():
    clock, port, radar = _setup(timeout=1.0)
    port.feed_after(0.1234, DUMP)
    radar.wait_for_hardware_trigger(timeout=5.0)
    assert radar.last_dump_read.first_byte_mono == pytest.approx(1000.1234)


def test_original_timeout_restored_including_none():
    clock, port, radar = _setup(timeout=None)
    port.feed_after(0.1, DUMP)
    radar.wait_for_hardware_trigger(timeout=5.0)   # must not raise WouldBlockForever
    assert port.timeout is None


def test_timeout_none_and_no_data_returns_by_deadline():
    clock, port, radar = _setup(timeout=None)
    assert radar.wait_for_hardware_trigger(timeout=2.0) == ""
    assert radar.last_dump_read.status is DumpStatus.NO_TRIGGER
    assert clock.monotonic() == pytest.approx(1002.0, abs=0.06)
    assert port.timeout is None


def test_timeout_restored_when_callback_raises_through():
    clock, port, radar = _setup(timeout=0.7)
    port.feed_after(0.1, DUMP)

    class Boom(Exception):
        pass

    def explode():
        raise Boom

    # The existing contract swallows callback errors; restoration must hold either way.
    radar.wait_for_hardware_trigger(timeout=5.0, on_first_byte=explode)
    assert port.timeout == 0.7


def test_cancel_before_first_byte_even_while_noise_streams():
    clock, port, radar = _setup()
    for i in range(200):
        port.feed_after(0.01 * i, b"noise ")
    cancel = threading.Event()
    cancel.set()
    assert radar.wait_for_hardware_trigger(timeout=30.0, cancel_event=cancel) == ""
    assert radar.last_dump_read.status is DumpStatus.CANCELLED
    assert clock.monotonic() < 1000.2


def test_cancel_after_first_byte_lets_the_dump_finish():
    clock, port, radar = _setup()
    cancel = threading.Event()
    port.feed_after(0.1, DUMP[:20])
    port.feed_after(0.3, DUMP[20:])
    radar.wait_for_hardware_trigger(timeout=5.0, cancel_event=cancel, on_first_byte=cancel.set)
    assert radar.last_dump_read.status is DumpStatus.COMPLETE


def test_stall_after_data_is_incomplete_with_evidence():
    clock, port, radar = _setup()
    port.feed_after(0.1, HEADER + I_LINE + b'{"Q":[2048,20')
    text = radar.wait_for_hardware_trigger(timeout=30.0)
    read = radar.last_dump_read
    assert read.status is DumpStatus.INCOMPLETE
    assert read.bytes_received == len(HEADER + I_LINE) + 13
    assert read.longest_gap_s >= OPS243Radar.DUMP_STALL_TOLERANCE_S
    assert text.endswith("20")  # evidence kept for the caller to reject and log


def test_gap_under_tolerance_still_completes():
    clock, port, radar = _setup()
    port.feed_after(0.1, DUMP[:30])
    port.feed_after(0.1 + OPS243Radar.DUMP_STALL_TOLERANCE_S - 0.1, DUMP[30:])
    radar.wait_for_hardware_trigger(timeout=30.0)
    assert radar.last_dump_read.status is DumpStatus.COMPLETE


def test_late_trigger_gets_its_dump_grace_past_the_timeout():
    clock, port, radar = _setup()
    port.feed_after(1.9, DUMP[:10])
    port.feed_after(3.5, DUMP[10:])
    radar.wait_for_hardware_trigger(timeout=2.0, dump_grace=8.0)
    assert radar.last_dump_read.status is DumpStatus.COMPLETE


def test_back_to_back_captures_do_not_leak_state():
    clock, port, radar = _setup()
    port.feed_after(0.1, DUMP)
    radar.wait_for_hardware_trigger(timeout=5.0)
    first = radar.last_dump_read
    port.feed_after(0.5, DUMP)
    radar.wait_for_hardware_trigger(timeout=5.0)
    assert radar.last_dump_read.status is DumpStatus.COMPLETE
    assert radar.last_dump_read.first_byte_mono > first.first_byte_mono


def test_completion_scan_is_linear_with_single_byte_reads(monkeypatch):
    clock, port, radar = _setup(timeout=0.05)
    big = HEADER + b'{"I":[' + b"2048," * 4095 + b'2048]}\r\n{"Q":[' + b"2048," * 4095 + b"2048]}"
    for index, byte in enumerate(big):
        port.feed(1000.1 + index * 1e-6, bytes([byte]))
    scanned = []
    original = radar._scan_new_bytes if hasattr(radar, "_scan_new_bytes") else None
    import openflight.ops243 as ops_module

    real_feed = ops_module._CompletionScanner.feed

    def counting_feed(self, data):
        scanned.append(len(data))
        return real_feed(self, data)

    monkeypatch.setattr(ops_module._CompletionScanner, "feed", counting_feed)
    radar.wait_for_hardware_trigger(timeout=5.0)
    assert radar.last_dump_read.status is DumpStatus.COMPLETE
    assert sum(scanned) == len(big)  # each byte scanned once: linear


def test_trigger_capture_uses_the_same_reader_and_sends_s_bang():
    clock, port, radar = _setup()
    port.on_write = lambda data: port.feed_after(0.02, DUMP) if data == b"S!\r" else None
    text = radar.trigger_capture(timeout=5.0)
    assert b"S!\r" in port.writes and text.endswith("]}")
    assert radar.last_dump_read.status is DumpStatus.COMPLETE
```

If `trigger_capture` writes a different command sequence, adapt the `on_write` trigger to the last command it sends before reading (`sed -n 1391,1430p src/openflight/ops243.py`). Delete the unused `original` line in `test_completion_scan_is_linear_with_single_byte_reads` when you implement it.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ops243_dump_reader.py -v`
Expected: FAIL (`ImportError: DumpStatus`).

- [ ] **Step 3: Implement**

In `ops243.py`, above the class:

```python
class DumpStatus(str, Enum):
    COMPLETE = "complete"  # a closed Q array arrived
    INCOMPLETE = "incomplete"  # data started but stalled or hit the deadline
    CANCELLED = "cancelled"  # cancel_event set before any dump byte
    NO_TRIGGER = "no_trigger"  # nothing arrived before the deadline


@dataclass(frozen=True)
class DumpRead:
    """One rolling-buffer dump read, with enough evidence to reject a bad one."""

    status: DumpStatus
    text: str
    bytes_received: int
    elapsed_s: float
    first_byte_wall: float | None
    first_byte_mono: float | None
    longest_gap_s: float


_CAPTURE_MARKERS = (b'{"sample_time"', b'{"trigger_time"')
_READ_TIMEOUT_S = 0.05  # bounds each blocking read so cancel stays responsive


class _CompletionScanner:
    """Finds the closed Q array by scanning each received byte once.

    Rebuilding and rescanning the whole response per chunk is quadratic, and
    blocking reads often return one byte at a time.
    """

    def __init__(self) -> None:
        self._tail = b""
        self._after_q = False
        self._depth = 0
        self._opened = False
        self._last_non_space = b""
        self.complete = False

    def feed(self, data: bytes) -> bool:
        window = self._tail + data
        start = len(self._tail)
        if not self._after_q:
            index = window.find(b'"Q"')
            if index < 0:
                self._tail = window[-2:]
                return False
            self._after_q = True
            start = index + 3
        for byte in window[start:]:
            char = bytes([byte])
            if char == b"[":
                self._depth += 1
                self._opened = True
            elif char == b"]":
                self._depth -= 1
            elif char == b"}" and self._last_non_space == b"]":
                self.complete = True
            if not char.isspace():
                self._last_non_space = char
        if self._opened and self._depth == 0 and self._last_non_space == b"]":
            self.complete = True
        self._tail = window[-2:]
        return self.complete


@contextmanager
def _serial_timeout(port, seconds: float):
    """Bound each read, restoring the caller's timeout (even None) on every exit."""
    original = port.timeout
    port.timeout = seconds
    try:
        yield
    finally:
        port.timeout = original
```

Imports: `from contextlib import contextmanager`, `from dataclasses import dataclass`, `from enum import Enum`.

The scanner treats `]` followed by `}` anywhere after `"Q"` as completion, which matches the old `"]}" in remaining`. The balanced-bracket rule matches the old `endswith("]")` with equal counts. Keep both.

In the class body add:

```python
    DUMP_STALL_TOLERANCE_S = 2.0
    _monotonic = staticmethod(time.monotonic)
    _wall = staticmethod(time.time)
    last_dump_read: "DumpRead | None" = None

    def _read_iq_dump(
        self,
        *,
        timeout_s: float,
        dump_grace_s: float,
        stall_tolerance_s: float,
        cancel_event: Optional[threading.Event],
        on_first_byte: Optional[Callable[[], None]],
        require_marker: bool,
    ) -> DumpRead:
        """Read one dump with bounded blocking reads; never sleeps between polls."""
        start = self._monotonic()
        deadline = start + timeout_s
        idle = bytearray()
        received = bytearray()
        scanner = _CompletionScanner()
        first_mono = first_wall = None
        last_data = None
        longest_gap = 0.0
        max_marker = max(len(m) for m in _CAPTURE_MARKERS)
        with _serial_timeout(self.serial, _READ_TIMEOUT_S):
            while True:
                now = self._monotonic()
                if first_mono is None and cancel_event is not None and cancel_event.is_set():
                    return self._finish(DumpStatus.CANCELLED, received, start, None, None, 0.0)
                if now >= deadline:
                    break
                if last_data is not None and now - last_data >= stall_tolerance_s:
                    longest_gap = max(longest_gap, now - last_data)
                    break
                waiting = self.serial.in_waiting
                chunk = self.serial.read(waiting if waiting else 1)
                if not chunk:
                    continue
                now = self._monotonic()
                if first_mono is None:
                    if require_marker:
                        idle.extend(chunk)
                        offsets = [o for o in (idle.find(m) for m in _CAPTURE_MARKERS) if o >= 0]
                        if not offsets:
                            del idle[:-max_marker]
                            continue
                        chunk = bytes(idle[min(offsets):])
                        idle.clear()
                    first_mono, first_wall = now, self._wall()
                    self.last_hardware_trigger_first_byte_timestamp = first_wall
                    deadline = max(deadline, first_mono + dump_grace_s)
                    if on_first_byte is not None:
                        try:
                            on_first_byte()
                        except Exception:  # pylint: disable=broad-exception-caught
                            logger.warning("[OPS] First-byte callback failed", exc_info=True)
                else:
                    longest_gap = max(longest_gap, now - last_data)
                last_data = now
                received.extend(chunk)
                if scanner.feed(chunk):
                    return self._finish(
                        DumpStatus.COMPLETE, received, start, first_wall, first_mono, longest_gap
                    )
        status = DumpStatus.NO_TRIGGER if first_mono is None else DumpStatus.INCOMPLETE
        return self._finish(status, received, start, first_wall, first_mono, longest_gap)

    def _finish(self, status, received, start, first_wall, first_mono, longest_gap) -> DumpRead:
        read = DumpRead(
            status=status,
            text=received.decode("ascii", errors="ignore"),
            bytes_received=len(received),
            elapsed_s=self._monotonic() - start,
            first_byte_wall=first_wall,
            first_byte_mono=first_mono,
            longest_gap_s=longest_gap,
        )
        self.last_dump_read = read
        return read
```

Rewrite `wait_for_hardware_trigger` to keep the docstring, then:

```python
        if not self.serial or not self.serial.is_open:
            raise ConnectionError("Not connected to radar")
        if dump_grace is None:
            dump_grace = self.transfer_budget_s(floor=8.0)
        self.last_hardware_trigger_first_byte_timestamp = None
        read = self._read_iq_dump(
            timeout_s=timeout,
            dump_grace_s=dump_grace,
            stall_tolerance_s=self.DUMP_STALL_TOLERANCE_S,
            cancel_event=cancel_event,
            on_first_byte=on_first_byte,
            require_marker=True,
        )
        self._log_dump_read("Hardware trigger", read, timeout)
        return read.text if read.status in (DumpStatus.COMPLETE, DumpStatus.INCOMPLETE) else ""
```

Keep the existing `reset_input_buffer()` at entry **for now**; Task 8 moves it under the OPS lock. Rewrite `trigger_capture` the same way with `require_marker=False`, after it writes `S!`. Keep its existing short-response warning inside `_log_dump_read`:

```python
    def _log_dump_read(self, label: str, read: DumpRead, timeout: float) -> None:
        if read.status is DumpStatus.NO_TRIGGER:
            logger.info("[OPS] %s: no data received within %.0fs", label, timeout)
        elif read.status is DumpStatus.INCOMPLETE:
            logger.warning(
                "[OPS] %s: dump incomplete, %d bytes in %.2fs (longest gap %.2fs)",
                label, read.bytes_received, read.elapsed_s, read.longest_gap_s,
            )
        elif read.status is DumpStatus.COMPLETE:
            logger.info("[OPS] %s: %d bytes in %.2fs", label, read.bytes_received, read.elapsed_s)
```

- [ ] **Step 4: Run the new tests and every existing OPS/rolling-buffer suite**

Run: `uv run pytest tests/test_ops243_dump_reader.py tests/test_ops243.py tests/test_ops243_uart.py tests/test_rolling_buffer.py tests/test_sound_trigger_serial_deadlock.py tests/test_capture_ops_spin.py tests/test_diagnose.py -q`
Expected: all pass.
- Existing `_ScheduledSerial` tests use real time and have no `timeout` attribute. Give `_ScheduledSerial` a `timeout = 1.0` attribute, and make its `read` sleep `min(timeout, 0.005)` when nothing is released, so it approximates blocking.
- If an existing test asserted on a `time.sleep` call count, rewrite it against `VirtualSerial`.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/ops243.py tests/test_ops243_dump_reader.py tests/test_ops243.py
git commit -m "ops: one bounded-blocking dump reader for S! and HOST_INT paths, linear completion scan"
```

---

### Task 7: Reject incomplete OPS dumps as measurement inputs (decision 6A, OPS side)

**Files:**
- Modify: `src/openflight/rolling_buffer/processor.py:174-245` (`parse_capture`: expected sample count)
- Modify: `src/openflight/rolling_buffer/trigger.py:559-680` (`SoundTrigger.wait_for_trigger`: incomplete rejection) and `SpeedTriggeredCapture` (`:168-320`, same check around its `trigger_capture` call)
- Modify: `src/openflight/rolling_buffer/types.py` (`IQCapture.first_byte_monotonic: Optional[float] = None`)
- Modify: `src/openflight/rolling_buffer/monitor.py` (pass `save_partial_dumps=debug` into the trigger's kwargs, next to the existing `trigger_kwargs`)
- Test: `tests/test_rolling_buffer.py`

**Interfaces:**
- Consumes: `DumpRead`, `DumpStatus`, `OPS243Radar.last_dump_read` (Task 6).
- Produces:
  - `RollingBufferProcessor(sample_rate, expected_samples: int | None = 4096)`.
  - `parse_capture` returns `None` when `len(I) != expected_samples`.
  - The `SoundTrigger` diagnostic reason is `"incomplete_dump"`, with keys `bytes_received`, `elapsed_s`, `longest_gap_s`, plus `partial_text` only when `save_partial_dumps`.
  - `IQCapture.first_byte_monotonic` is set from `DumpRead.first_byte_mono`.

- [ ] **Step 1: Write the failing tests**

```python
def _iq_response(n):
    samples = ",".join(["2048"] * n)
    return (
        '{"sample_time":1.0}\n{"trigger_time":1.1}\n'
        f'{{"I":[{samples}]}}\n{{"Q":[{samples}]}}'
    )


def test_parse_capture_rejects_equally_short_arrays():
    processor = RollingBufferProcessor(sample_rate=30000)
    assert processor.parse_capture(_iq_response(2048)) is None


def test_parse_capture_accepts_the_full_buffer():
    processor = RollingBufferProcessor(sample_rate=30000)
    assert processor.parse_capture(_iq_response(4096)) is not None


def test_parse_capture_sample_check_can_be_disabled_for_tools():
    processor = RollingBufferProcessor(sample_rate=30000, expected_samples=None)
    assert processor.parse_capture(_iq_response(4)) is not None


def test_sound_trigger_rejects_incomplete_dump_and_rearms(fake_radar_factory):
    radar = fake_radar_factory(
        response='{"sample_time":1.0}\n{"I":[1,2',
        dump_read=DumpRead(DumpStatus.INCOMPLETE, '{"sample_time":1.0}\n{"I":[1,2', 27, 2.3,
                           1.7e9, 1000.0, 2.1),
    )
    trigger = SoundTrigger(pre_trigger_segments=24)
    diagnostics = []
    trigger.diagnostic_sink = diagnostics.append  # use the file's existing diagnostic capture
    assert trigger.wait_for_trigger(radar, RollingBufferProcessor(30000)) is None
    assert radar.rearm_calls == [24]
    diag = diagnostics[-1]
    assert diag["reason"] == "incomplete_dump" and diag["bytes_received"] == 27
    assert "partial_text" not in diag


def test_sound_trigger_keeps_partial_text_only_in_diagnostic_mode(fake_radar_factory):
    radar = fake_radar_factory(
        response="{partial",
        dump_read=DumpRead(DumpStatus.INCOMPLETE, "{partial", 8, 2.0, 1.7e9, 1000.0, 2.0),
    )
    trigger = SoundTrigger(pre_trigger_segments=24, save_partial_dumps=True)
    diagnostics = []
    trigger.diagnostic_sink = diagnostics.append
    trigger.wait_for_trigger(radar, RollingBufferProcessor(30000))
    assert diagnostics[-1]["partial_text"] == "{partial"


def test_accepted_capture_carries_first_byte_monotonic(fake_radar_factory):
    ...  # complete DumpRead with first_byte_mono=1000.5 -> capture.first_byte_monotonic == 1000.5
```

Before writing these, read how `tests/test_rolling_buffer.py` fakes a radar for `SoundTrigger`, and how diagnostics are observed (`grep -n "_append_diagnostic\|diagnostic" src/openflight/rolling_buffer/trigger.py | head`). Implement `fake_radar_factory` as a local fixture that returns an object with `wait_for_hardware_trigger` (returning `response`), `last_dump_read`, `rearm_rolling_buffer` (recording segments), `last_hardware_trigger_first_byte_timestamp` and `read_clock_sync`. Replace `trigger.diagnostic_sink` with however the file already reads `_append_diagnostic` output. Write the last test in full, following the pattern of the first two.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_rolling_buffer.py -k "short_arrays or full_buffer or incomplete or partial_text or first_byte_monotonic" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`processor.py`:
- Add `expected_samples: Optional[int] = 4096` to `__init__`, and store it.
- In `parse_capture`, after the length-mismatch check:

```python
                if self.expected_samples is not None and len(i_samples) != self.expected_samples:
                    # Dropped whole lines leave equal but short arrays; the FFT
                    # would run on a shorter window and report plausible nonsense.
                    logger.warning(
                        "[PROCESSOR] I/Q has %d samples, expected %d, in a %d-byte response",
                        len(i_samples),
                        self.expected_samples,
                        len(response),
                    )
                    return None
```

  Check whether any existing test builds 4-sample responses through `parse_capture` with the default processor (`grep -n "parse_capture" tests/*.py | head -30`). Such tests either construct the processor with `expected_samples=None` or build 4096-sample fixtures. Prefer `expected_samples=None` in tests whose subject is not the sample count.

`trigger.py`, `SoundTrigger.__init__`: add `save_partial_dumps: bool = False`. In `wait_for_trigger`, right after the `if not response:` block:

```python
        dump_read = getattr(radar, "last_dump_read", None)
        if dump_read is not None and dump_read.status is DumpStatus.INCOMPLETE:
            # The radar has dumped and idles: rearm, and never measure a
            # truncated buffer.
            radar.rearm_rolling_buffer(self.pre_trigger_segments)
            evidence = {
                "bytes_received": dump_read.bytes_received,
                "elapsed_s": round(dump_read.elapsed_s, 3),
                "longest_gap_s": round(dump_read.longest_gap_s, 3),
            }
            if self.save_partial_dumps:
                evidence["partial_text"] = dump_read.text
            self._append_diagnostic(accepted=False, reason="incomplete_dump", **evidence)
            return None
```

  Check that `_append_diagnostic` accepts arbitrary keyword arguments (`sed -n "$(grep -n 'def _append_diagnostic' src/openflight/rolling_buffer/trigger.py | cut -d: -f1),+20p" src/openflight/rolling_buffer/trigger.py`). If it doesn't, extend it to merge `**extra` into the entry.
- After `capture = processor.parse_capture(...)` succeeds: `capture.first_byte_monotonic = dump_read.first_byte_mono if dump_read else None`.
- Apply the same INCOMPLETE rejection in `SpeedTriggeredCapture` after its `trigger_capture` call, using its existing reject and rearm path.

`types.py`: add the field `first_byte_monotonic: Optional[float] = None`, with the docstring line "Host monotonic time of the first dump byte (durations only; never compared with wall time)."

`rolling_buffer/monitor.py`: wherever `create_trigger(trigger_type, **trigger_kwargs)` is built, pass `save_partial_dumps` through for the sound trigger. The server passes `save_partial_dumps=args.debug`; add it to `start_monitor`'s `trigger_kwargs`.

- [ ] **Step 4: Run to verify**

Run: `uv run pytest tests/test_rolling_buffer.py tests/test_capture_ops_spin.py tests/test_diagnose.py tests/test_sound_trigger_serial_deadlock.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/rolling_buffer/ src/openflight/server.py tests/test_rolling_buffer.py
git commit -m "ops: incomplete and short dumps are rejected with evidence, never measured"
```

---

### Task 8: OPS serial lock, armed gate and `S!` request result (decision 4A, OPS side; 10A race test)

**Files:**
- Modify: `src/openflight/ops243.py`: `request_capture` (`:1380`), `rearm_rolling_buffer` (`:1605`), `wait_for_hardware_trigger` entry reset, `read_clock_sync` (`:564`), `__init__`
- Test: `tests/test_ops243_trigger_lock.py` (new)

**Interfaces:**
- Produces:
  - `class RequestResult(str, Enum)`: `SENT = "sent"`, `NOT_ARMED = "not_armed"`, `DISCONNECTED = "disconnected"`.
  - `OPS243Radar.request_capture() -> RequestResult`: no longer raises on disconnect; returns `DISCONNECTED`.
  - `OPS243Radar._io_lock: threading.RLock`: guards all writes, every `reset_input_buffer`, and `_armed`.
  - `OPS243Radar.rolling_buffer_armed: bool` (read-only property).
  - `OPS243Radar.last_rearm_mono: float | None`.
  - `OPS243Radar.last_request_mono: float | None`.
  - Lifecycle:
    - `prepare_persisted_rolling_buffer` and `rearm_rolling_buffer` set armed True **as their last action, under the lock, after `reset_input_buffer`**.
    - `request_capture` → `SENT` clears armed.
    - Seeing a dump's first byte clears armed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ops243_trigger_lock.py
"""S! and rearm share one lock: an S! never lands in a reset, and never reaches an unarmed buffer."""

import threading

from openflight.ops243 import OPS243Radar, RequestResult
from tests.serial_fakes import FakeClock, VirtualSerial


def _radar(port, clock):
    radar = OPS243Radar.__new__(OPS243Radar)
    radar.serial = port
    radar._io_lock = threading.RLock()
    radar._armed = False
    radar.last_rearm_mono = None
    radar.last_request_mono = None
    radar.last_dump_read = None
    radar.last_hardware_trigger_first_byte_timestamp = None
    radar._monotonic = clock.monotonic
    radar._wall = clock.time
    radar._sleep = clock.sleep
    radar.baud = 230_400
    return radar


def test_request_before_arming_is_refused_and_writes_nothing():
    clock = FakeClock()
    port = VirtualSerial(clock)
    radar = _radar(port, clock)
    assert radar.request_capture() is RequestResult.NOT_ARMED
    assert port.writes == []


def test_request_after_rearm_is_sent_once_and_disarms():
    clock = FakeClock()
    port = VirtualSerial(clock)
    radar = _radar(port, clock)
    radar.rearm_rolling_buffer(pre_trigger_segments=24)
    assert radar.rolling_buffer_armed and radar.last_rearm_mono is not None
    assert radar.request_capture() is RequestResult.SENT
    assert radar.request_capture() is RequestResult.NOT_ARMED
    assert port.writes.count(b"S!\r") == 1


def test_disconnected_port_reports_instead_of_raising():
    clock = FakeClock()
    port = VirtualSerial(clock)
    port.is_open = False
    radar = _radar(port, clock)
    assert radar.request_capture() is RequestResult.DISCONNECTED


def test_first_dump_byte_disarms():
    clock = FakeClock()
    port = VirtualSerial(clock)
    radar = _radar(port, clock)
    radar.rearm_rolling_buffer(24)
    port.feed_after(0.1, b'{"sample_time":1}\n{"I":[1]}\n{"Q":[1]}')
    radar.wait_for_hardware_trigger(timeout=2.0)
    assert not radar.rolling_buffer_armed


def test_s_bang_during_rearm_waits_for_the_lock_and_is_not_reset_away():
    """The race: IWR thread requests while the OPS thread is between drain and reset."""
    port_lock_entered = threading.Event()
    release_rearm = threading.Event()

    class SlowResetSerial(VirtualSerial):
        def reset_input_buffer(self):
            port_lock_entered.set()
            release_rearm.wait(2.0)
            super().reset_input_buffer()

    clock = FakeClock()
    port = SlowResetSerial(clock)
    radar = _radar(port, clock)
    radar._armed = True  # previous cycle armed; a dump just finished

    rearm = threading.Thread(target=radar.rearm_rolling_buffer, args=(24,))
    rearm.start()
    assert port_lock_entered.wait(2.0)

    results = []
    requester = threading.Thread(target=lambda: results.append(radar.request_capture()))
    requester.start()
    requester.join(0.2)
    assert requester.is_alive(), "S! must wait for the rearm to finish"
    release_rearm.set()
    rearm.join(2.0)
    requester.join(2.0)
    assert results == [RequestResult.SENT]
    writes = port.writes
    assert writes.index(b"S!\r") > max(i for i, w in enumerate(writes) if w.startswith(b"PA"))


def test_request_clears_armed_before_rearm_of_a_stale_cycle():
    clock = FakeClock()
    port = VirtualSerial(clock)
    radar = _radar(port, clock)
    radar.rearm_rolling_buffer(24)
    radar.request_capture()
    assert not radar.rolling_buffer_armed
```

`rearm_rolling_buffer` must use `self._sleep` and `self._monotonic` so the fake clock drives it. In the race test, `VirtualSerial.reset_input_buffer` is the moment between drain and reset.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ops243_trigger_lock.py -v`
Expected: FAIL (`ImportError: RequestResult`).

- [ ] **Step 3: Implement**

```python
class RequestResult(str, Enum):
    SENT = "sent"
    NOT_ARMED = "not_armed"  # rearm not finished, or a dump already in flight
    DISCONNECTED = "disconnected"
```

Class attributes, alongside Task 6's:

```python
    _sleep = staticmethod(time.sleep)
    _armed = False
    last_rearm_mono: Optional[float] = None
    last_request_mono: Optional[float] = None
```

In `__init__`: `self._io_lock = threading.RLock()`. Add a lazy accessor so `__new__`-built test radars also work:

```python
    @property
    def _lock(self) -> threading.RLock:
        lock = self.__dict__.get("_io_lock")
        if lock is None:
            lock = self.__dict__.setdefault("_io_lock", threading.RLock())
        return lock

    @property
    def rolling_buffer_armed(self) -> bool:
        return self._armed

    def request_capture(self) -> RequestResult:
        """Ask the armed rolling buffer to dump. The capture loop reads the reply.

        Held under the same lock as rearm: an S! written between the rearm's
        drain and its input reset would have its dump thrown away, and one
        written before PA reaches a buffer that is not sampling.
        """
        with self._lock:
            if not self.serial or not self.serial.is_open:
                return RequestResult.DISCONNECTED
            if not self._armed:
                logger.warning("[OPS] S! requested while not armed; ignored")
                return RequestResult.NOT_ARMED
            self.serial.write(b"S!\r")
            self.serial.flush()
            self._armed = False
            self.last_request_mono = self._monotonic()
            return RequestResult.SENT
```

In `rearm_rolling_buffer`:
- Wrap everything from the drain loop to the final log in `with self._lock:`.
- Set `self._armed = False` at the top.
- Replace `time.time()` with `self._monotonic()` and `time.sleep` with `self._sleep`.
- After `self.serial.reset_input_buffer()` add `self._armed = True` and `self.last_rearm_mono = self._monotonic()`.
- On the `SerialTimeoutException` early return, leave `_armed = False`.

In `prepare_persisted_rolling_buffer`, the final `rearm_rolling_buffer(...)` call arms it.

In `wait_for_hardware_trigger`, wrap the entry `reset_input_buffer()` in `with self._lock:` and **only reset when not armed**. When armed, the buffer may already hold an `S!`-requested dump:

```python
        with self._lock:
            if not self._armed:
                self.serial.reset_input_buffer()
```

In `_read_iq_dump`, at the first-byte branch, add `with self._lock: self._armed = False`.

In `read_clock_sync`, and in any other method that writes while the capture loop runs (`grep -n "self.serial.write" src/openflight/ops243.py`), wrap the write-and-read exchange in `with self._lock:`. `trigger_capture` (speed path) takes the lock around its own `S!` write. The speed path owns its own arming, so it does not check `_armed`.

Update the caller in `server.py` (`lambda _t, radar=ops_radar: radar.request_capture()`). It now returns a value; Task 11 records it.

- [ ] **Step 4: Run to verify, plus existing OPS suites**

Run: `uv run pytest tests/test_ops243_trigger_lock.py tests/test_ops243.py tests/test_ops243_rearm.py tests/test_ops243_uart.py tests/test_rolling_buffer.py tests/test_sound_trigger_serial_deadlock.py tests/test_ops243_dump_reader.py -q`
Expected: all pass. `test_sound_trigger_serial_deadlock.py` pins the "clock sync before rearm" ordering; that ordering must still hold, and the lock must not deadlock with it, because the `RLock` is re-entrant on the same thread.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/ops243.py tests/test_ops243_trigger_lock.py
git commit -m "ops: S! and rearm share one lock; S! only reaches an armed buffer and reports its result"
```

---

### Task 9: Typed incomplete IWR dumps, profile stall tolerance, partial evidence (decisions 6A, 8A)

**Files:**
- Modify: `src/openflight/iwr6843/driver.py`: `read_dump` (`:290-338`); add `IncompleteDumpError`, a clock class attribute and `DEFAULT_STALL_TOLERANCE_S = 8.0`
- Modify: `src/openflight/iwr6843/monitor.py`: `IWR6843Capture` (add `error_kind`), `__init__` (add `dump_stall_tolerance_s`), `_read_capture`'s l3dump branch (`:633`), `_capture` (`:661-711`)
- Modify: `src/openflight/server.py` (`init_iwr6843` passes `dump_stall_tolerance_s` into the monitor)
- Modify callers of `read_dump` that relied on partial returns: `src/openflight/iwr6843/firmware_checks.py:778`, `src/openflight/iwr6843/late_window.py:338`, `scripts/iwr6843/swing_trigger.py:215`, `scripts/hardware-test/iwr6843_iq8_hwa_probe.py:62`
- Test: `tests/test_iwr6843_driver.py`, `tests/test_iwr6843_monitor.py`

**Interfaces:**
- Produces:
  - `class IncompleteDumpError(RuntimeError)`, with attributes `received: int`, `expected: int | None`, `elapsed_s: float`, `longest_gap_s: float`, `partial: bytes`.
  - `IWR6843Radar.read_dump(timeout_s=40.0, stall_tolerance_s=DEFAULT_STALL_TOLERANCE_S) -> bytes`: raises `IncompleteDumpError` instead of returning short data.
  - `IWR6843Capture.error_kind: str | None`: `"incomplete_dump"`, `"readback_failed"` or `None`.
  - `IWR6843CaptureMonitor(..., dump_stall_tolerance_s: float = 8.0)`.
  - Partial evidence path: `<capture_path>.partial.l3dump` when `save_dumps`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_iwr6843_driver.py`, using `VirtualSerial`, the fake clock, and a real header built from the in-repo capture. Find it with `git ls-files | grep l3_dump_configurable_capture_20260818`:

```python
REAL_DUMP = Path(<path from git ls-files>).read_bytes()


def _iwr(clock, port):
    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = port
    radar._trigger_pending = b""
    radar._monotonic = clock.monotonic
    return radar


def test_full_dump_across_random_chunks_returns_exactly_the_payload():
    rng = random.Random(20260927)
    clock = FakeClock()
    port = VirtualSerial(clock, timeout=0.3)
    stream = b"l3dump\r\n" + REAL_DUMP + b"\r\nDone\r\n"
    t, i = 0.01, 0
    while i < len(stream):
        size = rng.randint(1, 8192)
        port.feed_after(t, stream[i : i + size])
        i += size
        t += rng.uniform(0.0, 0.2)
    assert _iwr(clock, port).read_dump(timeout_s=60.0) == REAL_DUMP


def test_stall_past_tolerance_raises_with_evidence():
    clock = FakeClock()
    port = VirtualSerial(clock, timeout=0.3)
    port.feed_after(0.01, REAL_DUMP[:200_000])
    port.feed_after(0.01 + 9.0, REAL_DUMP[200_000:])  # 9 s gap > 8 s tolerance
    with pytest.raises(IncompleteDumpError) as excinfo:
        _iwr(clock, port).read_dump(timeout_s=60.0)
    error = excinfo.value
    assert error.received == 200_000 and error.expected == len(REAL_DUMP)
    assert error.longest_gap_s >= 8.0 and error.partial == REAL_DUMP[:200_000]
    assert "200000/" in str(error)


def test_stall_just_under_tolerance_completes():
    clock = FakeClock()
    port = VirtualSerial(clock, timeout=0.3)
    port.feed_after(0.01, REAL_DUMP[:200_000])
    port.feed_after(0.01 + 7.5, REAL_DUMP[200_000:] + b"\r\nDone\r\n")
    assert _iwr(clock, port).read_dump(timeout_s=60.0) == REAL_DUMP


def test_no_magic_before_deadline_raises_with_nothing_expected():
    clock = FakeClock()
    port = VirtualSerial(clock, timeout=0.3)
    port.feed_after(0.01, b"l3dump\r\nError: not frozen\r\n")
    with pytest.raises(IncompleteDumpError) as excinfo:
        _iwr(clock, port).read_dump(timeout_s=2.0)
    assert excinfo.value.expected is None


def test_read_dump_sends_l3dump_exactly_once_even_after_a_stall():
    clock = FakeClock()
    port = VirtualSerial(clock, timeout=0.3)
    port.feed_after(0.01, REAL_DUMP[:1000])
    with pytest.raises(IncompleteDumpError):
        _iwr(clock, port).read_dump(timeout_s=30.0)
    assert port.writes.count(b"l3dump\n") == 1
```

Adapt `_iwr` to whatever else `read_dump` touches, such as `_discard_before_readback` and `_wait_for_dump_cli_ready` (`sed -n 139,160p src/openflight/iwr6843/driver.py`). Set only the attributes those methods read.

In `tests/test_iwr6843_monitor.py`:

```python
def test_incomplete_dump_capture_is_invalid_and_saves_partial_in_debug(tmp_path):
    radar = _fake_radar()
    radar.read_dump = lambda **_: (_ for _ in ()).throw(
        IncompleteDumpError(received=10, expected=100, elapsed_s=9.0, longest_gap_s=8.2,
                            partial=b"x" * 10)
    )
    monitor = _monitor(radar=radar, save_dumps=True, output_dir=tmp_path, full_capture=True)
    capture = _run_one_capture(monitor)   # existing helper that queues an edge and waits
    assert not capture.valid and capture.error_kind == "incomplete_dump"
    partial = list(tmp_path.glob("*.partial.l3dump"))
    assert len(partial) == 1 and partial[0].read_bytes() == b"x" * 10


def test_incomplete_dump_without_debug_saves_nothing(tmp_path):
    ...  # same with save_dumps=False -> no files written, error_kind still "incomplete_dump"


def test_monitor_passes_its_stall_tolerance_to_read_dump():
    seen = {}
    radar = _fake_radar()
    radar.read_dump = lambda **kw: seen.update(kw) or VALID_DUMP
    monitor = _monitor(radar=radar, dump_stall_tolerance_s=5.5, full_capture=True)
    _run_one_capture(monitor)
    assert seen["stall_tolerance_s"] == 5.5
```

Write the second test in full (the `...` body is described). Use the existing helpers in `tests/test_iwr6843_monitor.py` for building a monitor whose `_read_capture` takes the l3dump path, and for running one capture. If they're named differently, use theirs; if none exist, write `_run_one_capture(monitor)` to call `monitor._capture(timeline_or_edge)` directly.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_iwr6843_driver.py -k "dump" tests/test_iwr6843_monitor.py -k "incomplete or stall_tolerance" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`driver.py`:

```python
DEFAULT_STALL_TOLERANCE_S = 8.0  # CP2105 stalls of several seconds were seen mid-dump on range sessions


class IncompleteDumpError(RuntimeError):
    """A dump that stopped short. Never retried: the firmware re-arms after
    streaming, so a second l3dump returns a different capture."""

    def __init__(self, *, received, expected, elapsed_s, longest_gap_s, partial):
        self.received = received
        self.expected = expected
        self.elapsed_s = elapsed_s
        self.longest_gap_s = longest_gap_s
        self.partial = partial
        super().__init__(
            f"IWR6843 dump incomplete: {received}/{expected if expected is not None else '?'} "
            f"bytes in {elapsed_s:.1f}s (longest gap {longest_gap_s:.1f}s)"
        )
```

- In `IWR6843Radar`, add `_monotonic = staticmethod(time.monotonic)`.
- In `read_dump`, replace `time.time()` with `self._monotonic()` and track `longest_gap`.
- On stall, log `logger.warning("[IWR6843] Dump stream stalled >= %.1fs, giving up (%d/%s bytes received)", ...)`, as weawer did in `a6533a3`, then raise.
- Replace `if expected is None: return bytes(buf)` with `raise IncompleteDumpError(received=len(buf), expected=None, ...)`.
- Add `if len(buf) < expected: raise IncompleteDumpError(...)` before slicing.
- Change the default to `stall_tolerance_s: float = DEFAULT_STALL_TOLERANCE_S`.
- Update the module docstring gotcha about stall tolerance as weawer's `c5be7623` did.

`monitor.py`:
- Add `error_kind: str | None = None` to `IWR6843Capture`.
- Store `self.dump_stall_tolerance_s`.
- In `_read_capture`, call `self.radar.read_dump(stall_tolerance_s=self.dump_stall_tolerance_s)`.
- In `_capture`, replace the broad `except` with:

```python
        except IncompleteDumpError as exc:
            error, error_kind, raw = str(exc), "incomplete_dump", None
            logger.warning("[IWR6843] Capture #%d: %s", sequence, exc)
            if self.save_dumps and exc.partial:
                partial_path = self._capture_path(sequence, edge_timestamp).with_suffix(
                    ".partial.l3dump"
                )
                partial_path.write_bytes(exc.partial)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            error, error_kind, raw = str(exc), "readback_failed", None
            logger.warning("[IWR6843] Capture #%d failed: %s", sequence, exc, exc_info=True)
```

  Initialise `error_kind = None` before the `try`, and pass `error_kind=error_kind` into `IWR6843Capture(...)`. Replace `time.time()` durations in `_capture` with `time.monotonic()`, keeping `completed_timestamp` as wall-clock time for the record.

Callers:
- `late_window.py:338` and `firmware_checks.py:778` must catch `IncompleteDumpError` where they previously checked the length. Read each call site and convert its short-dump branch into an `except IncompleteDumpError` with the same outcome and message.
- For the two scripts, let the exception propagate with its message.

- [ ] **Step 4: Run to verify**

Run: `uv run pytest tests/test_iwr6843_driver.py tests/test_iwr6843_monitor.py tests/test_iwr6843_late_window.py tests/test_iwr6843_firmware_checks.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/driver.py src/openflight/iwr6843/monitor.py src/openflight/iwr6843/late_window.py src/openflight/server.py scripts/iwr6843/swing_trigger.py scripts/hardware-test/iwr6843_iq8_hwa_probe.py tests/test_iwr6843_driver.py tests/test_iwr6843_monitor.py tests/test_iwr6843_late_window.py
git add -p src/openflight/iwr6843/firmware_checks.py tests/test_iwr6843_firmware_checks.py
git commit -m "iwr: incomplete dumps raise with received/expected/gap evidence, 8 s stall tolerance, partial kept in debug"
```

---

### Task 10: IWR recovery before ready, and bounded captures (decisions 7A, 14A)

**Files:**
- Modify: `src/openflight/iwr6843/monitor.py`:
  - `__init__`: add state fields and the `on_unmatched` callback parameter
  - `notify_trigger` (`:500`): refuse while recovering or needing attention
  - `_capture` (`:661`): run recovery after a failed capture
  - add `_recover_radar`, `health_state`, `_prune_captures_locked`
- Test: `tests/test_iwr6843_recovery_state.py` (new)

**Interfaces:**
- Consumes: `IWR6843Radar.drain_stale_output`, `stats`, `_apply_self_trigger` (existing); `IncompleteDumpError` (Task 9).
- Produces:
  - `IWR6843CaptureMonitor.health_state -> str`: one of `"ready"`, `"disarmed"`, `"capturing"`, `"recovering"`, `"needs_attention"`.
  - `IWR6843CaptureMonitor.needs_attention: bool`.
  - `IWR6843CaptureMonitor(..., on_unmatched: Callable[[IWR6843Capture, str], None] | None = None)`.
  - Constants: `_RECOVERY_ATTEMPTS = 2`, `MAX_PENDING_CAPTURES = 4`, `UNMATCHED_MAX_AGE_S = 30.0`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_iwr6843_recovery_state.py
"""After a failed readback the monitor drains, checks health and re-arms before accepting edges."""

import pytest

from openflight.iwr6843.driver import IncompleteDumpError
from openflight.iwr6843.monitor import MAX_PENDING_CAPTURES, IWR6843Capture

# Use the builders from tests/test_iwr6843_monitor.py (import or copy _fake_radar/_monitor).


def _failing_dump(**_):
    raise IncompleteDumpError(received=5, expected=50, elapsed_s=9.0, longest_gap_s=8.1,
                              partial=b"12345")


def test_failed_capture_drains_checks_health_and_rearms_before_ready():
    calls = []
    radar = _fake_radar()
    radar.read_dump = _failing_dump
    radar.drain_stale_output = lambda **kw: calls.append(("drain", kw)) or 1234
    radar.stats = lambda: calls.append(("stats",)) or "frames=9 active=1\nDone\n"
    radar.cmd = lambda line, window=1.5: calls.append(("cmd", line)) or "Done\n"
    monitor = _monitor(radar=radar, self_trigger=_self_trigger_config(), full_capture=True)
    _run_one_capture(monitor)
    kinds = [c[0] for c in calls]
    assert kinds[:2] == ["drain", "stats"]
    assert ("cmd", monitor.self_trigger.command) in calls
    assert monitor.health_state == "ready" and not monitor.needs_attention


def test_drain_waits_out_a_full_stall_window():
    seen = {}
    radar = _fake_radar()
    radar.read_dump = _failing_dump
    radar.drain_stale_output = lambda **kw: seen.update(kw) or 0
    radar.stats = lambda: "active=1\nDone\n"
    monitor = _monitor(radar=radar, dump_stall_tolerance_s=8.0, full_capture=True)
    _run_one_capture(monitor)
    assert seen["stream_quiet_s"] >= 8.0 and seen["max_wait_s"] > seen["stream_quiet_s"]


def test_radar_that_stays_inactive_needs_attention_and_refuses_edges():
    radar = _fake_radar()
    radar.read_dump = _failing_dump
    radar.drain_stale_output = lambda **kw: 0
    radar.stats = lambda: "active=0\nDone\n"
    monitor = _monitor(radar=radar, full_capture=True)
    _run_one_capture(monitor)
    assert monitor.needs_attention and monitor.health_state == "needs_attention"
    assert monitor.notify_trigger() is False


def test_recovery_is_bounded_when_the_health_check_raises():
    attempts = []
    radar = _fake_radar()
    radar.read_dump = _failing_dump
    radar.drain_stale_output = lambda **kw: attempts.append(1) or 0
    radar.stats = lambda: (_ for _ in ()).throw(TimeoutError("no prompt"))
    monitor = _monitor(radar=radar, full_capture=True)
    _run_one_capture(monitor)
    assert len(attempts) == 2 and monitor.needs_attention


def test_edges_are_refused_while_recovering():
    monitor = _monitor(radar=_fake_radar())
    monitor._recovering = True
    assert monitor.notify_trigger() is False


def test_successful_capture_does_not_run_recovery():
    radar = _fake_radar()
    radar.drain_stale_output = lambda **kw: pytest.fail("no recovery after a good capture")
    monitor = _monitor(radar=radar, full_capture=True)
    _run_one_capture(monitor)
    assert monitor.health_state == "ready"


def test_unmatched_captures_are_bounded_and_reported():
    dropped = []
    monitor = _monitor(radar=_fake_radar(), on_unmatched=lambda c, why: dropped.append((c.sequence, why)))
    for sequence in range(1, MAX_PENDING_CAPTURES + 3):
        monitor._append_capture(_capture(sequence, trigger_timestamp=1000.0 + sequence))
    assert len(monitor._captures) == MAX_PENDING_CAPTURES
    assert dropped == [(1, "count"), (2, "count")]


def test_old_unmatched_captures_are_pruned_by_age():
    dropped = []
    monitor = _monitor(radar=_fake_radar(), on_unmatched=lambda c, why: dropped.append((c.sequence, why)))
    monitor._append_capture(_capture(1, trigger_timestamp=1000.0))
    monitor._append_capture(_capture(2, trigger_timestamp=1000.0 + 31.0))
    assert [c.sequence for c in monitor._captures] == [2]
    assert dropped == [(1, "age")]


def test_a_capture_awaiting_its_shot_within_the_age_is_kept():
    monitor = _monitor(radar=_fake_radar())
    monitor._append_capture(_capture(1, trigger_timestamp=1000.0))
    monitor._append_capture(_capture(2, trigger_timestamp=1012.0))
    assert [c.sequence for c in monitor._captures] == [1, 2]


def _capture(sequence, trigger_timestamp):
    return IWR6843Capture(sequence=sequence, trigger_timestamp=trigger_timestamp,
                          completed_timestamp=trigger_timestamp + 1, dump_duration_s=1.0,
                          raw=b"x", path=None)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_iwr6843_recovery_state.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `monitor.py`:

```python
_RECOVERY_ATTEMPTS = 2
MAX_PENDING_CAPTURES = 4
# Longer than the OPS capture loop's 30 s wait, so a shot still being
# processed always finds its capture.
UNMATCHED_MAX_AGE_S = 30.0
```

In `__init__`: `self._recovering = False`, `self.needs_attention = False`, `self._on_unmatched = on_unmatched`.

In `notify_trigger`, add `or self._recovering or self.needs_attention` to the busy condition.

In `_capture`, after building `capture`:

```python
        with self._condition:
            self._append_capture(capture)
            self._capture_active = capture.error is not None  # stay busy through recovery
            self._condition.notify_all()
        if capture.error is not None:
            self._recovering = True
            try:
                self.needs_attention = not self._recover_radar()
            finally:
                self._recovering = False
                with self._condition:
                    self._capture_active = False
                    self._condition.notify_all()
```

`_append_capture` replaces the direct `self._captures.append(capture)` and must be called with the condition held:

```python
    def _append_capture(self, capture: IWR6843Capture) -> None:
        """Keep the capture for its shot; drop old or excess ones, saying why."""
        self._captures.append(capture)
        newest = capture.trigger_timestamp
        while self._captures and newest - self._captures[0].trigger_timestamp > UNMATCHED_MAX_AGE_S:
            self._drop_unmatched(self._captures.popleft(), "age")
        while len(self._captures) > MAX_PENDING_CAPTURES:
            self._drop_unmatched(self._captures.popleft(), "count")

    def _drop_unmatched(self, capture: IWR6843Capture, reason: str) -> None:
        logger.warning(
            "[IWR6843] Dropping unmatched capture #%d (%s)", capture.sequence, reason
        )
        if self._on_unmatched is not None:
            try:
                self._on_unmatched(capture, reason)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[IWR6843] Unmatched-capture callback failed", exc_info=True)
```

`_append_capture` takes no lock itself. The tests call it directly without the lock, which is fine because they are single-threaded.

```python
    def _recover_radar(self) -> bool:
        """Back to a known armed state after a failed readback, or False.

        A stalled dump can finish streaming later: drain until the line has
        been quiet for a full stall window, confirm the front end still runs,
        then re-send the trigger. l3dump is never retried (the ring re-armed).
        """
        for attempt in range(1, _RECOVERY_ATTEMPTS + 1):
            try:
                drained = self.radar.drain_stale_output(
                    max_wait_s=self.dump_stall_tolerance_s + 4.0,
                    initial_quiet_s=0.25,
                    stream_quiet_s=self.dump_stall_tolerance_s,
                )
                health = self.radar.stats()
                if "active=1" not in health:
                    logger.error(
                        "[IWR6843] Recovery %d: front end not active (%s); restart the kiosk",
                        attempt,
                        health.strip(),
                    )
                    return False
                self._apply_self_trigger()
                logger.info("[IWR6843] Recovered after failed readback (drained %d bytes)", drained)
                return True
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[IWR6843] Recovery attempt %d failed", attempt, exc_info=True)
        return False

    @property
    def health_state(self) -> str:
        if self.needs_attention:
            return "needs_attention"
        if self._recovering:
            return "recovering"
        if self._capture_active or self._edge_pending:
            return "capturing"
        return "ready" if self._armed else "disarmed"
```

Check that `self._armed` is the actual attribute name (`grep -n "_armed" src/openflight/iwr6843/monitor.py`). In the server, pass `on_unmatched` from `init_iwr6843`. It calls the Task 11 session logger method; until Task 11, pass a lambda that logs through `logger`.

- [ ] **Step 4: Run to verify**

Run: `uv run pytest tests/test_iwr6843_recovery_state.py tests/test_iwr6843_monitor.py tests/test_iwr6843_detect_queue.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/iwr6843/monitor.py src/openflight/server.py tests/test_iwr6843_recovery_state.py
git commit -m "iwr: drain, health-check and re-arm before accepting edges; bound unmatched captures"
```

---

### Task 11: Trigger ownership wiring and the monotonic trigger timeline (decisions 4A, 10A, 15A)

**Files:**
- Create: `src/openflight/trigger_timeline.py`
- Modify: `src/openflight/iwr6843/monitor.py`:
  - `notify_trigger` (`:500`): relay first, build the timeline
  - `_events`: queue `TriggerTimeline`
  - `_capture`: take the timeline, stamp completion and rearm
  - `IWR6843Capture.timeline`
  - add `set_ops_relay()`
- Modify: `src/openflight/server.py`:
  - `start_monitor` (`:4196-4205`): register the relay by mode
  - the shot path at `iwr6843_runtime.process_shot(` (`:2722`): log the timeline
  - `init_iwr6843`: `on_unmatched` goes to the session logger
- Modify: `src/openflight/session_logger.py` (add `log_trigger_timeline`, `log_iwr_capture_unmatched`)
- Test: `tests/test_trigger_timeline.py` (new), `tests/test_iwr6843_trigger_ownership.py` (new), `tests/test_server.py`, `tests/test_session_logger.py`

**Interfaces:**
- Consumes: `TriggerMode` (Task 4), `RequestResult` (Task 8), `IQCapture.first_byte_monotonic` (Task 7), `health_state` (Task 10).
- Produces:
  - `@dataclass class TriggerTimeline`, with fields:
    - `edge_wall: float`, `notice_mono: float`
    - `ops_request_mono: float | None = None`, `ops_request_result: str | None = None`
    - `ops_first_byte_mono: float | None = None`
    - `impact_wall: float | None = None`, `impact_source: str | None = None`, `impact_confidence: str | None = None`
    - `capture_completed_mono: float | None = None`, `rearmed_mono: float | None = None`
  - Methods: `.durations() -> dict[str, float | None]`, `.as_log() -> dict`.
  - `IMPACT_CONFIDENCE = {"ops_clock_sync": "high", "first_byte": "low"}`; unknown sources map to `"unknown"`.
  - `NOTICE_TO_REQUEST_WARN_MS = 5.0`.
  - `IWR6843CaptureMonitor.set_ops_relay(relay: Callable[[], RequestResult] | None) -> None`.
  - `IWR6843Capture.timeline: TriggerTimeline | None = None`.
  - `SessionLogger.log_trigger_timeline(timeline: dict, *, mode: str, shot_number: int | None)` writes entry type `"trigger_timeline"`.
  - `SessionLogger.log_iwr_capture_unmatched(sequence: int, reason: str, age_s: float | None, error_kind: str | None)` writes entry type `"iwr_capture_unmatched"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_trigger_timeline.py
"""Durations only ever subtract host monotonic stamps."""

import pytest

from openflight.trigger_timeline import IMPACT_CONFIDENCE, TriggerTimeline


def test_durations_from_monotonic_stamps():
    t = TriggerTimeline(edge_wall=1.7e9, notice_mono=100.0, ops_request_mono=100.002,
                        ops_request_result="sent", ops_first_byte_mono=100.010,
                        capture_completed_mono=102.5, rearmed_mono=103.0)
    d = t.durations()
    assert d["notice_to_request_ms"] == pytest.approx(2.0)
    assert d["request_to_first_byte_ms"] == pytest.approx(8.0)
    assert d["notice_to_complete_s"] == pytest.approx(2.5)
    assert d["notice_to_rearm_s"] == pytest.approx(3.0)


def test_missing_stamps_give_none_not_garbage():
    d = TriggerTimeline(edge_wall=1.7e9, notice_mono=100.0).durations()
    assert set(d.values()) == {None}


def test_impact_confidence_comes_from_the_source():
    t = TriggerTimeline(edge_wall=1.7e9, notice_mono=1.0)
    t.set_impact(1.7e9 + 0.01, "ops_clock_sync")
    assert t.impact_confidence == "high"
    t.set_impact(1.7e9 + 0.01, "first_byte")
    assert t.impact_confidence == "low"
    t.set_impact(1.7e9 + 0.01, "mystery")
    assert t.impact_confidence == "unknown"
    assert IMPACT_CONFIDENCE["ops_clock_sync"] == "high"


def test_as_log_includes_stamps_and_durations():
    log = TriggerTimeline(edge_wall=1.7e9, notice_mono=1.0).as_log()
    assert log["notice_mono"] == 1.0 and "durations" in log
```

```python
# tests/test_iwr6843_trigger_ownership.py
"""Mode x camera x event matrix: observers fire once per accepted edge; S! only in iwr_primary."""

import itertools

import pytest

from openflight.ops243 import RequestResult

MODES = ("iwr_primary", "ops_independent")
CAMERA = (True, False)
EVENTS = ("accepted", "duplicate", "disarmed", "busy")


@pytest.mark.parametrize(("mode", "camera", "event"), list(itertools.product(MODES, CAMERA, EVENTS)))
def test_trigger_matrix(mode, camera, event):
    relay_calls, camera_calls, order = [], [], []

    def relay():
        relay_calls.append(1)
        order.append("relay")
        return RequestResult.SENT

    observers = []
    if camera:
        observers.append(lambda ts: (camera_calls.append(ts), order.append("camera")))
    monitor = _monitor(radar=_fake_radar(), trigger_observers=observers,
                       self_trigger=_self_trigger_config())
    monitor._running = True
    monitor._armed = event != "disarmed"
    if mode == "iwr_primary":
        monitor.set_ops_relay(relay)
    if event == "busy":
        monitor._capture_active = True
    if event == "duplicate":
        assert monitor.notify_trigger(timestamp=1000.0) is True
        relay_calls.clear(); camera_calls.clear(); order.clear()
        accepted = monitor.notify_trigger(timestamp=1000.05)
    else:
        accepted = monitor.notify_trigger(timestamp=1000.0)

    expected_accept = event == "accepted"
    assert accepted is expected_accept
    assert len(relay_calls) == (1 if expected_accept and mode == "iwr_primary" else 0)
    assert len(camera_calls) == (1 if expected_accept and camera else 0)
    if expected_accept and mode == "iwr_primary" and camera:
        assert order == ["relay", "camera"]  # S! is not delayed by the camera


def test_accepted_edge_queues_a_timeline_with_request_result():
    monitor = _monitor(radar=_fake_radar(), self_trigger=_self_trigger_config())
    monitor._running = True
    monitor._armed = True
    monitor.set_ops_relay(lambda: RequestResult.NOT_ARMED)
    monitor.notify_trigger(timestamp=1000.0)
    timeline = monitor._events.get_nowait()
    assert timeline.edge_wall == 1000.0
    assert timeline.ops_request_result == "not_armed"
    assert timeline.ops_request_mono >= timeline.notice_mono


def test_relay_exception_is_recorded_and_camera_still_fires():
    camera = []
    monitor = _monitor(radar=_fake_radar(), trigger_observers=[camera.append],
                       self_trigger=_self_trigger_config())
    monitor._running = True
    monitor._armed = True
    monitor.set_ops_relay(lambda: (_ for _ in ()).throw(OSError("port gone")))
    assert monitor.notify_trigger(timestamp=1000.0) is True
    assert camera == [1000.0]
    assert monitor._events.get_nowait().ops_request_result == "error"


def test_rejected_self_trigger_notice_is_released_not_relayed():
    relay = []
    radar = _fake_radar()
    radar.wait_trigger_notice = lambda pending: (True, b"")
    released = []
    radar.release_sparse_freeze = lambda: released.append(1)
    monitor = _monitor(radar=radar, self_trigger=_self_trigger_config())
    monitor._running = True
    monitor._armed = False  # disarmed
    monitor.set_ops_relay(lambda: relay.append(1) or RequestResult.SENT)
    monitor._listen_for_self_trigger()
    assert released == [1] and relay == []


def test_capture_stamps_completion_and_rearm_on_its_timeline():
    monitor = _monitor(radar=_fake_radar(), full_capture=True)
    capture = _run_one_capture(monitor)
    t = capture.timeline
    assert t.capture_completed_mono is not None and t.rearmed_mono >= t.capture_completed_mono


def test_notice_to_request_latency_warns_above_threshold(caplog, monkeypatch):
    ...  # monotonic stamps 10 ms apart -> one WARNING containing "notice->S!"
```

Write the last test in full. Patch `openflight.iwr6843.monitor.time.monotonic` with an iterator that returns `100.0` and then `100.010`.

In `tests/test_server.py`:

```python
@pytest.mark.parametrize(("mode", "relayed"), [("iwr_primary", True), ("ops_independent", False)])
def test_start_monitor_registers_the_relay_only_in_iwr_primary(monkeypatch, mode, relayed):
    ...  # fake iwr6843_runtime.capture_monitor records set_ops_relay calls and arm();
         # set server.trigger_mode = TriggerMode(mode); run the non-mock start_monitor branch
         # with a fake RollingBufferMonitor; assert relay is set iff relayed and arm() is called
         # in both modes.


def test_ops_independent_never_writes_s_bang(monkeypatch):
    ...  # same harness with a recording OPS radar: after one accepted IWR edge, the OPS
         # radar's request_capture was never called.
```

Use the existing `start_monitor` harness in `tests/test_server.py`, if there is one (`grep -n "def test_.*start_monitor\|start_monitor(" tests/test_server.py | head`), and write both tests fully against it. If there is none, build one with `monkeypatch.setattr(server, "RollingBufferMonitor", FakeMonitor)`.

In `tests/test_session_logger.py`: both new methods write their entry type and fields, and neither writes when the logger is disabled. Follow the existing `log_connection` test.

Server shot-path test: when `process_shot` returns a result whose `capture.timeline` is set, `trigger_timeline` is logged once with `ops_first_byte_mono` taken from the shot's OPS capture, and with `impact_source`/`impact_confidence` from its `trigger_timestamp_source`. Find where the shot's `IQCapture` is reachable in `on_shot_detected` / the IWR processing stage (`grep -n "first_byte_timestamp\|trigger_timestamp_source" src/openflight/server.py | head`). Thread `first_byte_monotonic` and `trigger_timestamp_source` onto `Shot`, the same way `trigger_timestamp_source` already flows (`rolling_buffer/monitor.py:589`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_trigger_timeline.py tests/test_iwr6843_trigger_ownership.py tests/test_server.py -k "relay or s_bang or timeline" tests/test_session_logger.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# src/openflight/trigger_timeline.py
"""When each part of one trigger happened, on the host monotonic clock.

Durations subtract monotonic stamps only. The wall-clock edge and impact
times are kept for matching devices and for the record, never subtracted
from monotonic stamps, and firmware ticks never enter this at all.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

IMPACT_CONFIDENCE = {"ops_clock_sync": "high", "first_byte": "low"}
NOTICE_TO_REQUEST_WARN_MS = 5.0


def _ms(later: float | None, earlier: float | None) -> float | None:
    return None if later is None or earlier is None else (later - earlier) * 1000.0


def _s(later: float | None, earlier: float | None) -> float | None:
    return None if later is None or earlier is None else later - earlier


@dataclass
class TriggerTimeline:
    edge_wall: float
    notice_mono: float
    ops_request_mono: float | None = None
    ops_request_result: str | None = None
    ops_first_byte_mono: float | None = None
    impact_wall: float | None = None
    impact_source: str | None = None
    impact_confidence: str | None = None
    capture_completed_mono: float | None = None
    rearmed_mono: float | None = None

    def set_impact(self, impact_wall: float | None, source: str | None) -> None:
        self.impact_wall = impact_wall
        self.impact_source = source
        self.impact_confidence = IMPACT_CONFIDENCE.get(source or "", "unknown")

    def durations(self) -> dict[str, float | None]:
        return {
            "notice_to_request_ms": _ms(self.ops_request_mono, self.notice_mono),
            "request_to_first_byte_ms": _ms(self.ops_first_byte_mono, self.ops_request_mono),
            "notice_to_complete_s": _s(self.capture_completed_mono, self.notice_mono),
            "notice_to_rearm_s": _s(self.rearmed_mono, self.notice_mono),
        }

    def as_log(self) -> dict:
        return {**asdict(self), "durations": self.durations()}
```

`monitor.py`:
- Add `timeline: TriggerTimeline | None = None` to `IWR6843Capture`.
- Add `self._ops_relay = None` in `__init__`, plus:

```python
    def set_ops_relay(self, relay) -> None:
        """The OPS S! request, called first on each accepted edge (iwr_primary only)."""
        self._ops_relay = relay
```

In `notify_trigger`, keep the guard block. Replace the `put_nowait(edge_timestamp)` and observer loop with:

```python
            timeline = TriggerTimeline(edge_wall=edge_timestamp, notice_mono=time.monotonic())
            self._events.put_nowait(timeline)
            self._condition.notify_all()
        if self._ops_relay is not None:
            try:
                result = self._ops_relay()
                timeline.ops_request_result = getattr(result, "value", str(result))
            except Exception:  # pylint: disable=broad-exception-caught
                timeline.ops_request_result = "error"
                logger.warning("[IWR6843] OPS S! relay failed", exc_info=True)
            timeline.ops_request_mono = time.monotonic()
            latency_ms = (timeline.ops_request_mono - timeline.notice_mono) * 1000.0
            if latency_ms > NOTICE_TO_REQUEST_WARN_MS:
                logger.warning("[IWR6843] notice->S! took %.1f ms", latency_ms)
        for observer in self._trigger_observers:
            ...  # unchanged
        return True
```

Mutating `timeline` after `put_nowait` is safe. The worker only reads `ops_request_*` at log time, long after, and both fields are set before `notify_trigger` returns. The worker's `_capture(timeline)` does not read them.

- Wherever the worker takes an event, check for the `_STOP` and job sentinels, then call `self._capture(event)`.
- `_capture(self, timeline)` uses `edge_timestamp = timeline.edge_wall`. At the end it sets `timeline.capture_completed_mono = time.monotonic()` once the dump is done, and `timeline.rearmed_mono = time.monotonic()` after recovery. On success, rearm happens when `_capture_active` is cleared.
- Set `capture.timeline = timeline`.
- The GPIO path's `notify_trigger(timestamp)` is unchanged for callers.
- Check every place that puts a float into `_events` (`grep -n "_events.put" src/openflight/iwr6843/monitor.py`) and convert it to a `TriggerTimeline`.

`server.start_monitor`:

```python
        if iwr6843_runtime is not None:
            capture_monitor = iwr6843_runtime.capture_monitor
            if trigger_mode is TriggerMode.IWR_PRIMARY:
                ops_radar = getattr(monitor, "radar", None)
                if ops_radar is None or not hasattr(ops_radar, "request_capture"):
                    raise RuntimeError("IWR6843 self-trigger needs an OPS radar that accepts S!")
                capture_monitor.set_ops_relay(ops_radar.request_capture)
            capture_monitor.arm()
```

Shot path. After `shot_result = iwr6843_runtime.process_shot(...)`, when `shot_result.capture is not None and shot_result.capture.timeline is not None`:

```python
        timeline = shot_result.capture.timeline
        timeline.ops_first_byte_mono = shot.first_byte_monotonic
        timeline.set_impact(shot.trigger_timestamp, shot.trigger_timestamp_source)
        session_logger.log_trigger_timeline(
            timeline.as_log(), mode=trigger_mode.value, shot_number=shot.shot_number
        )
```

Use the actual attribute names on `Shot` (`grep -n "trigger_timestamp_source\|shot_number\|first_byte" src/openflight/launch_monitor.py`), adding `first_byte_monotonic: Optional[float] = None` to `Shot` if it is missing, and populate it where `trigger_timestamp_source` is copied from the `IQCapture`.

`session_logger.py`: add both methods, following `log_connection`:

```python
    def log_trigger_timeline(self, timeline: Dict[str, Any], *, mode: str, shot_number=None):
        """One accepted trigger's host-monotonic stamps and derived durations."""
        if not self.enabled:
            return
        self._write_entry("trigger_timeline", {"mode": mode, "shot_number": shot_number, **timeline})

    def log_iwr_capture_unmatched(self, sequence: int, reason: str, age_s=None, error_kind=None):
        """An IWR capture dropped without a shot (waggle, OPS rejection, or overflow)."""
        if not self.enabled:
            return
        self._write_entry(
            "iwr_capture_unmatched",
            {"sequence": sequence, "reason": reason, "age_s": age_s, "error_kind": error_kind},
        )
```

In `init_iwr6843`, pass `on_unmatched=lambda capture, reason: _log_unmatched(capture, reason)`, where `_log_unmatched` gets the session logger (`get_session_logger()`) and computes `age_s = time.time() - capture.trigger_timestamp` (wall minus wall).

- [ ] **Step 4: Run to verify**

Run: `uv run pytest tests/test_trigger_timeline.py tests/test_iwr6843_trigger_ownership.py tests/test_server.py tests/test_session_logger.py tests/test_iwr6843_monitor.py tests/test_iwr6843_detect_queue.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/trigger_timeline.py src/openflight/iwr6843/monitor.py src/openflight/server.py src/openflight/session_logger.py src/openflight/launch_monitor.py src/openflight/rolling_buffer/ tests/test_trigger_timeline.py tests/test_iwr6843_trigger_ownership.py tests/test_server.py tests/test_session_logger.py
git commit -m "trigger: one owner per mode, relay before camera, monotonic trigger timeline in the session log"
```

---

### Task 12: Documentation and full verification

**Files:**
- Modify: `docs/changelog.md` (new entry at the top, in the file's style)
- Modify: `CLAUDE.md`, section "Running the Application" (add the profile examples below)
- Create: `docs/iwr6843/kiosk-profiles.md` (what profiles are, the two shipped profiles, trigger modes table, how to read `session_start.kiosk`, `trigger_timeline` and `iwr_capture_unmatched`, plus the firmware reflash note for `stats caps`)

- [ ] **Step 1: Write the docs**

CLAUDE.md addition, under "Running the Application":

```bash
scripts/start-kiosk.sh --iwr6843-profile iq16-2ms    # default: 2 ms adaptive16, IWR primary trigger
scripts/start-kiosk.sh --iwr6843-profile wide-3ms    # previous wide 3 ms IQ16 capture (comparison / recovery)
```

- [ ] **Step 2: Run the full suite and linters**

Run each command and read its output:
- `uv run pytest tests/ -q`
- `uv run pylint src/openflight/ --fail-under=9`
- `uv run ruff check src/openflight/`
- `uv run ruff format --check src/openflight/`

Expected: every test passes (skips unchanged apart from the documented compiler skip), pylint ≥ 9.0, and ruff clean. Fix anything that fails before committing, and never mark a failure as passing.

- [ ] **Step 3: Commit**

```bash
git add docs/changelog.md CLAUDE.md docs/iwr6843/kiosk-profiles.md
git commit -m "docs: kiosk profiles, trigger modes and the new session log entries"
```

---

## Self-Review Notes (plan author)

**Spec coverage:**
- **Stage 1** is covered by Tasks 3, 4 and 5: firmware id, profile, calibration, tee/bin, trigger mode, onboard flags, the log and refusal. Per the user's 2026-09-27 decision, the 2 ms adaptive16 profile is the default (`iq16-2ms`), and the previous capture stays selectable (`wide-3ms`).
- **Stage 2:**
  - 2A: Task 2 (bias ±, override, window edges, follow mode).
  - 2B: Task 6 (blocking reads, cancel, fragments, timeout restored) and Task 8 (lock).
  - 2C: Tasks 7 and 9 (evidence, rejection, 8 s tolerance, no retry) and Task 10 (recover before ready).
- **Stage 3:** Task 4 (validation), Task 8 (no extra `S!`, armed gate) and Task 11 (relay by mode, observers once, camera in both modes, timeline with the six stamps, monotonic time).

**Known gaps, on purpose:**
- Stage 3's "OPS diagnostic mode never receives an extra `S!`" is covered by `test_ops_independent_never_writes_s_bang`. Hardware confirmation belongs to the stage 6 acceptance session.
- The 2 ms profile uses the existing `diagnostic_24f2ms_53bin_iq16.cfg` until stage 6 builds a planner-budgeted cfg.
