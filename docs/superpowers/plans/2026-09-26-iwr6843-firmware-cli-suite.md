# IWR6843 Firmware CLI Test Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Pi-side command that exercises every command the shipped IWR6843 firmware registers on its CLI, prints PASS/FAIL/SKIP per check, exits non-zero on any FAIL, and is itself unit-tested without hardware.

**Architecture:** All check logic lives in a new library module `src/openflight/iwr6843/firmware_checks.py` (parsers, a `Context`, check functions grouped into `Section`s, and a runner). A thin script `scripts/hardware-test/test_iwr_firmware.py` parses arguments and calls the runner. Unit tests drive every check through a `ScriptedSerial` fake that answers CLI lines from a reply table, so prompts, sleeps, and the clock are injected and no test touches a port.

**Tech Stack:** Python 3.11+, `uv`, pytest, numpy, pyserial (via the existing `IWR6843Radar` driver). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-26-iwr6843-firmware-cli-suite-design.md`

## Global Constraints

- Always run Python through `uv run` (`uv run pytest`, `uv run pylint`, `uv run ruff`). Never bare `python`/`pytest`.
- `uv run pylint src/openflight/ --fail-under=9`, `uv run ruff check src/openflight/`, and `uv run ruff format --check src/openflight/` must pass at every commit that touches `src/`.
- Hardware-test scripts start with `sys.path.insert(0, "src")` and import `openflight.*` after it, with `# noqa: E402`, exactly like `scripts/hardware-test/iwr6843_cadence_soak.py`.
- Check names printed by the runner are the exact strings in the spec's catalogue (for example `trigger/fresh session untriggered`).
- The only enforced timing bound is readback under 1.0 s per shot. Every other timing number is reported in the detail string, never judged.
- The on-chip solve has no CLI entry point in this image. It is a SKIP, never a fake PASS.
- Commit after every task with the `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` trailer.

## Review Focus

1. **A `Triggered` notice that arrives inside a `stats` reply** while the swing section polls must still be seen by `wait_trigger_notice`; dropping it leaves the ring frozen and the check hangs to its deadline. Pinned in Task 8 (`test_swing_notice_inside_a_stats_reply_is_seen`).
2. **An older firmware image without `l3track`, `debugCfg`, or the rearm stats line** must produce SKIP lines, not tracebacks or FAILs that mask real regressions. Pinned in Task 3 (`test_unsupported_command_becomes_skip`) and Task 2 (`test_snapshot_reports_missing_fields_as_none`).
3. **A check that raises mid-way (serial timeout, wedged CLI)** must not leave the sensor frozen, armed, or streaming debug lines for the next section or the operator. Pinned in Task 3 (`test_cleanup_runs_after_a_raising_check`, `test_cleanup_failure_forces_exit_1`).
4. **A stats line whose compound fields (`used=100/200`, `plan=24pre/0post`, `calib=0x0`) shift position or gain a field** must still parse; the cadence soak already partially relies on this. Pinned in Task 2 (`test_snapshot_parses_all_four_real_stats_lines`).
5. **`--only` with a section name that does not exist, or `--swing` combined with `--only lifecycle`,** must fail before a port is opened rather than half-run. Pinned in Task 3 (`test_run_rejects_unknown_section_names`) and Task 9 (`test_cli_rejects_unknown_only_before_opening_a_port`).

---

## File map

| Path | Responsibility |
|---|---|
| `src/openflight/iwr6843/firmware_checks.py` | Parsers (`parse_stats`, `parse_trig`, `StatsSnapshot`), `Context`, result/check/section types, helpers (`stats_snapshot`, `wait_until`, `read_port_text`, `ensure_sensor`, `track_config_command`), the six section builders, `SECTIONS`, `run`, `cleanup`, `exit_code`, `write_json`. Expected to reach ~700 lines; that is the one place all hardware checks live, mirroring `monitor.py` in size. |
| `scripts/hardware-test/test_iwr_firmware.py` | argparse, port-name guard, `--list`, open the radar, build the `Context`, call `run` and `cleanup`, print the summary, exit code. |
| `tests/iwr6843_fakes.py` | Adds `ScriptedSerial`. |
| `tests/test_iwr6843_firmware_checks.py` | All unit tests for the library. |
| `tests/test_iwr6843_firmware_cli.py` | Tests for the script's argument handling and `--list`. |
| `scripts/hardware-test/iwr6843_cadence_soak.py` | Imports `parse_stats` from the library. |
| `scripts/iwr6843/swing_trigger.py` | Imports `parse_trig` from the library. |
| `tests/test_iwr6843_monitor.py`, `tests/test_swing_trigger.py` | Parser tests re-pointed at the library. |
| `scripts/hardware-test/test_iwr_self_trigger.py` | Deleted in Task 6. |
| `docs/iwr6843/verify.md`, `docs/development/firmware.md` | Document the suite. |

---

### Task 1: Shared parsers and `StatsSnapshot`

**Files:**
- Create: `src/openflight/iwr6843/firmware_checks.py`
- Create: `tests/test_iwr6843_firmware_checks.py`
- Modify: `scripts/hardware-test/iwr6843_cadence_soak.py:58-68` (delete local `parse_stats`, import it)
- Modify: `scripts/iwr6843/swing_trigger.py:50-64` (delete local `parse_trig`, import it)
- Modify: `tests/test_iwr6843_monitor.py:1081-1117` (parser tests call the library)
- Modify: `tests/test_swing_trigger.py:55-65` (parser test calls the library)

**Interfaces:**
- Produces:
  - `parse_stats(text: str) -> dict[str, int]` — every `key=<digits>` pair, same regex as the soak had.
  - `parse_trig(line: str) -> dict[str, str] | None` — fields after `trig ` on a stats or debug line; `None` without `phase` and `tee`.
  - `@dataclass(frozen=True) class StatsSnapshot` with fields `active: int | None`, `frames: int | None`, `pre_seen: int | None`, `plan_pre: int | None`, `plan_post: int | None`, `freeze_req: int | None`, `freeze_done: int | None`, `format: str | None`, `stride: int | None`, `used: int | None`, `capacity: int | None`, `rearm_last_us: int | None`, `rearm_max_us: int | None`, `rearm_timed: int | None`, `detect_dropped: int | None`, `detect_stale: int | None`, `phase: str | None`, `tee: int | None`, `latched: int | None`, `enabled: int | None`, `raw: str`.
  - `parse_snapshot(text: str) -> StatsSnapshot`.

- [ ] **Step 1: Write the failing parser tests**

Create `tests/test_iwr6843_firmware_checks.py`:

```python
"""Unit tests for the IWR6843 firmware CLI check suite (no hardware)."""

from __future__ import annotations

from openflight.iwr6843 import firmware_checks as fc

# Verbatim shape of the four lines l3_cli_stats writes (firmware/iwr6843/l3_dump.c).
STATS_ACTIVE = (
    "frames=112345 wraps=4 active=1 calib=0x0 rf_faults=0 "
    "hwa_frames=112345 hwa_out=112345 hwa_rearms=112344 hwa_rearm_err=0 "
    "hwa_missed=10 freeze_req=2 freeze_done=2 freeze_to=0 "
    "format=iq16 plan=16pre/8post loops=12 used=737280/786432\n"
    "iq8_packed=0 iq8_overrun=0 iq8_clipped=0 pending=0 pre_seen=112345 "
    "post_kept=0 post_seen=0 stride=1 iq8_edma_done=0 iq8_edma_err=0 "
    "iq8_edma_wait=0 iq8_busy=0/1 iq8_scale=128\n"
    "trig phase=tee-low tee=412 latched=0 enabled=1\n"
    "detect dropped=0 stale=0\n"
    "rearm_last_us=120 rearm_max_us=310 rearm_timed=112344\n"
    "Done\n"
)


def test_parse_stats_reads_every_integer_field():
    stats = fc.parse_stats(STATS_ACTIVE)

    assert stats["frames"] == 112345
    assert stats["hwa_missed"] == 10
    assert stats["freeze_done"] == 2
    assert stats["rearm_max_us"] == 310


def test_parse_trig_reads_stats_and_debug_lines():
    stats = fc.parse_trig("trig phase=watching tee=1800 latched=0 enabled=1")
    debug = fc.parse_trig(
        "trig phase=toward tee=10 approach=4 ready=1 toward=1 away=0 "
        "run=3 peak=8 have=1 bin=14 level=1000 latched=0"
    )

    assert stats == {"phase": "watching", "tee": "1800", "latched": "0", "enabled": "1"}
    assert debug["phase"] == "toward"
    assert debug["level"] == "1000"
    assert fc.parse_trig("frames=1 active=1") is None


def test_snapshot_parses_all_four_real_stats_lines():
    snap = fc.parse_snapshot(STATS_ACTIVE)

    assert snap.active == 1
    assert snap.frames == 112345
    assert snap.pre_seen == 112345
    assert (snap.plan_pre, snap.plan_post) == (16, 8)
    assert (snap.freeze_req, snap.freeze_done) == (2, 2)
    assert snap.format == "iq16"
    assert snap.stride == 1
    assert (snap.used, snap.capacity) == (737280, 786432)
    assert (snap.rearm_last_us, snap.rearm_max_us, snap.rearm_timed) == (120, 310, 112344)
    assert (snap.detect_dropped, snap.detect_stale) == (0, 0)
    assert (snap.phase, snap.tee, snap.latched, snap.enabled) == ("tee-low", 412, 0, 1)
    assert snap.raw == STATS_ACTIVE


def test_snapshot_reports_missing_fields_as_none():
    snap = fc.parse_snapshot("frames=5 wraps=0 active=0 calib=0x0 rf_faults=0\nDone\n")

    assert snap.active == 0
    assert snap.plan_pre is None
    assert snap.rearm_max_us is None
    assert snap.phase is None
    assert snap.latched is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'openflight.iwr6843.firmware_checks'`

- [ ] **Step 3: Create the module with the parsers**

Create `src/openflight/iwr6843/firmware_checks.py`:

```python
"""Hardware checks for the IWR6843 L3-dump firmware CLI.

Everything the hardware-test script ``scripts/hardware-test/test_iwr_firmware.py``
decides lives here so it can be unit-tested against a scripted serial port.
The checks talk to the firmware through ``IWR6843Radar`` only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_INT_FIELD = re.compile(r"(\w+)=(\d+)")
_USED = re.compile(r"used=(\d+)/(\d+)")
_PLAN = re.compile(r"plan=(\d+)pre/(\d+)post")
_FORMAT = re.compile(r"format=(iq8|iq16)")
_DETECT = re.compile(r"detect dropped=(\d+) stale=(\d+)")


def parse_stats(text: str) -> dict[str, int]:
    """Every ``key=<digits>`` pair of a firmware ``stats`` response.

    Compound fields (``format=iq8``, ``plan=24pre/0post``, ``used=100/200``,
    ``calib=0x0``) partially match on their leading digits; ``parse_snapshot``
    reads those with their own patterns.
    """
    return {key: int(value) for key, value in _INT_FIELD.findall(text)}


def parse_trig(line: str) -> dict[str, str] | None:
    """Fields from a ``trig phase=...`` debug or stats line."""
    text = line.strip()
    marker = text.find("trig ")
    if marker < 0 or "phase=" not in text[marker:]:
        return None
    fields: dict[str, str] = {}
    for token in text[marker + len("trig ") :].split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        fields[key] = value
    if "phase" not in fields or "tee" not in fields:
        return None
    return fields


@dataclass(frozen=True)
class StatsSnapshot:
    """Typed view of one ``stats`` reply; ``None`` where the image omits a field."""

    raw: str
    active: int | None = None
    frames: int | None = None
    pre_seen: int | None = None
    plan_pre: int | None = None
    plan_post: int | None = None
    freeze_req: int | None = None
    freeze_done: int | None = None
    format: str | None = None
    stride: int | None = None
    used: int | None = None
    capacity: int | None = None
    rearm_last_us: int | None = None
    rearm_max_us: int | None = None
    rearm_timed: int | None = None
    detect_dropped: int | None = None
    detect_stale: int | None = None
    phase: str | None = None
    tee: int | None = None
    latched: int | None = None
    enabled: int | None = None


def _trig_int(fields: dict[str, str] | None, key: str) -> int | None:
    if not fields or key not in fields:
        return None
    try:
        return int(float(fields[key]))
    except ValueError:
        return None


def parse_snapshot(text: str) -> StatsSnapshot:
    """Parse a whole ``stats`` reply into a ``StatsSnapshot``."""
    ints = parse_stats(text)
    used = _USED.search(text)
    plan = _PLAN.search(text)
    fmt = _FORMAT.search(text)
    detect = _DETECT.search(text)
    trig = None
    for line in text.splitlines():
        parsed = parse_trig(line)
        if parsed is not None:
            trig = parsed
    return StatsSnapshot(
        raw=text,
        active=ints.get("active"),
        frames=ints.get("frames"),
        pre_seen=ints.get("pre_seen"),
        plan_pre=int(plan.group(1)) if plan else None,
        plan_post=int(plan.group(2)) if plan else None,
        freeze_req=ints.get("freeze_req"),
        freeze_done=ints.get("freeze_done"),
        format=fmt.group(1) if fmt else None,
        stride=ints.get("stride"),
        used=int(used.group(1)) if used else None,
        capacity=int(used.group(2)) if used else None,
        rearm_last_us=ints.get("rearm_last_us"),
        rearm_max_us=ints.get("rearm_max_us"),
        rearm_timed=ints.get("rearm_timed"),
        detect_dropped=int(detect.group(1)) if detect else None,
        detect_stale=int(detect.group(2)) if detect else None,
        phase=trig.get("phase") if trig else None,
        tee=_trig_int(trig, "tee"),
        latched=_trig_int(trig, "latched"),
        enabled=_trig_int(trig, "enabled"),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -v`
Expected: 4 passed

- [ ] **Step 5: Make the cadence soak import the shared parser**

In `scripts/hardware-test/iwr6843_cadence_soak.py`, delete the `parse_stats` function (lines 58-68) and change the import block to:

```python
sys.path.insert(0, "src")

from openflight.iwr6843.driver import IWR6843Radar  # noqa: E402
from openflight.iwr6843.firmware_checks import parse_stats  # noqa: E402
```

Keep `parse_stats` referenced by name in `main()` exactly as before. The module attribute `soak.parse_stats` still exists (it is now the imported function), so `tests/test_iwr6843_monitor.py` keeps working unchanged. Remove `import re` from the soak if nothing else uses it (check with `uv run ruff check scripts/hardware-test/iwr6843_cadence_soak.py`).

- [ ] **Step 6: Make swing_trigger import the shared parser**

In `scripts/iwr6843/swing_trigger.py`, delete the `parse_trig` function (lines 50-64) and add to the imports:

```python
from openflight.iwr6843.firmware_checks import parse_trig
```

`is_latched`, `format_status`, and `tests/test_swing_trigger.py` keep referring to `swing_trigger.parse_trig`, which is now the imported name; no test edits are needed.

- [ ] **Step 7: Run the affected suites and linters**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py tests/test_iwr6843_monitor.py tests/test_swing_trigger.py -q`
Expected: all passed

Run: `uv run ruff check src/openflight/ scripts/ && uv run ruff format --check src/openflight/ && uv run pylint src/openflight/iwr6843/firmware_checks.py --fail-under=9`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add src/openflight/iwr6843/firmware_checks.py tests/test_iwr6843_firmware_checks.py scripts/hardware-test/iwr6843_cadence_soak.py scripts/iwr6843/swing_trigger.py
git commit -m "refactor(iwr6843): share the firmware stats and trig parsers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `ScriptedSerial` fake and the `Context`/result types

**Files:**
- Modify: `tests/iwr6843_fakes.py` (append `ScriptedSerial`)
- Modify: `src/openflight/iwr6843/firmware_checks.py` (append types and helpers)
- Modify: `tests/test_iwr6843_firmware_checks.py`

**Interfaces:**
- Produces (fakes):
  - `class ScriptedSerial(replies: dict[str, bytes | Callable[[int], bytes]], *, unknown: bytes = b"'{cmd}' is not recognized as a CLI command\n")`. A written line is matched first by its full stripped text, then by its first token. A callable reply receives how many times that key has been sent so far (starting at 0). `.written: list[str]` records every line. `.inject(data: bytes)` queues bytes ahead of the next read. `.reset_input_buffer()` clears unread bytes like the real port. `in_waiting`, `read(n)`, `write(data)` match `serial.Serial`.
  - `scripted_radar(replies, **kw) -> IWR6843Radar` — an `IWR6843Radar` built with `__new__` whose `.ser` is a `ScriptedSerial` and `.port == "scripted"`.
- Produces (library):
  - `CheckResult(name: str, status: str, detail: str = "", seconds: float = 0.0)`; `PASS = "PASS"`, `FAIL = "FAIL"`, `SKIP = "SKIP"`.
  - `Check(name: str, run: Callable[[Context], CheckResult], needs_swing: bool = False)`.
  - `Section(name: str, sensor: str, checks: tuple[Check, ...])` with `sensor` in `{"active", "stopped", "any"}`.
  - `@dataclass class Context(radar, config: str, tee_m: float, level: float | None, hits: int, wait_s: float, shots: int, profiles: tuple[str, ...], prompt: Callable[[str], None], sleep: Callable[[float], None], clock: Callable[[], float], out: Callable[[str], None])`.
  - `stats_snapshot(ctx) -> StatsSnapshot` (sends `stats`).
  - `wait_until(ctx, predicate: Callable[[], bool], timeout_s: float, poll_s: float = 0.1) -> bool`.
  - `read_port_text(ctx, seconds: float) -> str` — raw CLI text arriving within a window.
  - `ensure_sensor(ctx, state: str) -> None` — `send_config` for `"active"`, `stop_sensor` for `"stopped"`, no-op for `"any"` or when already there.
  - `passed(name, detail="") / failed(name, detail="") / skipped(name, detail="")` constructors.

- [ ] **Step 1: Write the failing fake and helper tests**

Append to `tests/test_iwr6843_firmware_checks.py`:

```python
from tests.iwr6843_fakes import ScriptedSerial, scripted_radar


STATS_STOPPED = "frames=0 wraps=0 active=0 calib=0x0 rf_faults=0\ntrig phase=off tee=0 latched=0 enabled=0\nDone\n"


def _ctx(radar, **overrides) -> fc.Context:
    ticks = {"now": 0.0}

    def clock():
        ticks["now"] += 0.05
        return ticks["now"]

    fields = dict(
        radar=radar,
        config="config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",
        tee_m=1.575,
        level=1000.0,
        hits=2,
        wait_s=5.0,
        shots=2,
        profiles=("config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",),
        prompt=lambda _text: None,
        sleep=lambda _s: None,
        clock=clock,
        out=lambda _line: None,
    )
    fields.update(overrides)
    return fc.Context(**fields)


def test_scripted_serial_matches_full_line_then_first_token():
    port = ScriptedSerial({"triggerCfg 0 0 0": b"Done\n", "triggerCfg": b"Error: trigger bin\n"})

    port.write(b"triggerCfg 0 0 0\n")
    off = port.read(port.in_waiting)
    port.write(b"triggerCfg x 1 2\n")
    bad = port.read(port.in_waiting)
    port.write(b"bogus\n")
    unknown = port.read(port.in_waiting)

    assert off == b"Done\n"
    assert bad == b"Error: trigger bin\n"
    assert b"not recognized" in unknown
    assert port.written == ["triggerCfg 0 0 0", "triggerCfg x 1 2", "bogus"]


def test_scripted_serial_callable_replies_count_sends_and_inject_precedes_reads():
    port = ScriptedSerial({"stats": lambda n: f"frames={n * 100} active=1\nDone\n".encode()})

    port.inject(b"Triggered\n")
    port.write(b"stats\n")
    first = port.read(port.in_waiting)
    port.write(b"stats\n")
    second = port.read(port.in_waiting)

    assert first == b"Triggered\nframes=0 active=1\nDone\n"
    assert second == b"frames=100 active=1\nDone\n"


def test_scripted_serial_reset_drops_unread_bytes():
    port = ScriptedSerial({})
    port.inject(b"stale")

    port.reset_input_buffer()

    assert port.in_waiting == 0


def test_stats_snapshot_sends_stats_and_parses_the_reply():
    radar = scripted_radar({"stats": STATS_ACTIVE.encode()})

    snap = fc.stats_snapshot(_ctx(radar))

    assert radar.ser.written == ["stats"]
    assert snap.active == 1 and snap.phase == "tee-low"


def test_wait_until_polls_until_true_or_deadline():
    calls = {"n": 0}

    def predicate():
        calls["n"] += 1
        return calls["n"] >= 3

    ctx = _ctx(scripted_radar({}))

    assert fc.wait_until(ctx, predicate, timeout_s=5.0) is True
    assert calls["n"] == 3
    assert fc.wait_until(ctx, lambda: False, timeout_s=0.2) is False


def test_read_port_text_collects_bytes_until_the_window_closes():
    radar = scripted_radar({})
    radar.ser.inject(b"trig phase=tee-low tee=5\n")

    text = fc.read_port_text(_ctx(radar), seconds=0.3)

    assert "phase=tee-low" in text


def test_ensure_sensor_stops_an_active_sensor_and_starts_a_stopped_one(monkeypatch):
    calls: list[str] = []
    radar = scripted_radar({"stats": lambda n: (STATS_ACTIVE if n == 0 else STATS_STOPPED).encode()})
    monkeypatch.setattr(radar, "stop_sensor", lambda: calls.append("stop"))
    monkeypatch.setattr(radar, "send_config", lambda cfg: calls.append(f"start:{cfg}"))
    ctx = _ctx(radar, config="wide.cfg")

    fc.ensure_sensor(ctx, "stopped")
    fc.ensure_sensor(ctx, "active")
    fc.ensure_sensor(ctx, "any")

    assert calls == ["stop", "start:wide.cfg"]


def test_ensure_sensor_is_a_no_op_when_already_in_state(monkeypatch):
    calls: list[str] = []
    radar = scripted_radar({"stats": STATS_ACTIVE.encode()})
    monkeypatch.setattr(radar, "send_config", lambda cfg: calls.append("start"))

    fc.ensure_sensor(_ctx(radar), "active")

    assert calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -v`
Expected: FAIL with `ImportError: cannot import name 'ScriptedSerial'`

- [ ] **Step 3: Add `ScriptedSerial` and `scripted_radar` to the fakes**

Append to `tests/iwr6843_fakes.py`:

```python
class ScriptedSerial:
    """Serial port that answers CLI lines from a reply table.

    A written line matches first by its full stripped text, then by its
    first token. A callable reply receives how many times that key was
    sent before (starting at 0), so ``stats`` can show counters advancing.
    ``inject`` queues bytes ahead of the next read, the way a ``Triggered``
    notice or a ``trig`` debug line arrives unasked.
    """

    def __init__(
        self,
        replies: dict[str, bytes | Callable[[int], bytes]],
        *,
        unknown: bytes = b"'{cmd}' is not recognized as a CLI command\n",
    ):
        self._replies = dict(replies)
        self._unknown = unknown
        self._buffer = bytearray()
        self._sent: dict[str, int] = {}
        self.written: list[str] = []

    @property
    def in_waiting(self) -> int:
        return len(self._buffer)

    def read(self, count: int) -> bytes:
        count = min(count, len(self._buffer))
        chunk = bytes(self._buffer[:count])
        del self._buffer[:count]
        return chunk

    def write(self, data: bytes) -> None:
        line = data.decode(errors="replace").strip()
        self.written.append(line)
        key = line if line in self._replies else line.split(" ", 1)[0]
        reply = self._replies.get(key)
        if reply is None:
            self._buffer += self._unknown.replace(b"{cmd}", line.encode())
            return
        count = self._sent.get(key, 0)
        self._sent[key] = count + 1
        self._buffer += reply(count) if callable(reply) else reply

    def inject(self, data: bytes) -> None:
        self._buffer += data

    def reset_input_buffer(self) -> None:
        self._buffer.clear()


def scripted_radar(replies: dict, **kwargs) -> "IWR6843Radar":
    """An ``IWR6843Radar`` on a ``ScriptedSerial`` without opening a port."""
    from openflight.iwr6843.driver import IWR6843Radar  # pylint: disable=import-outside-toplevel

    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = ScriptedSerial(replies, **kwargs)
    radar.port = "scripted"
    radar._trigger_pending = b""  # pylint: disable=protected-access
    return radar
```

- [ ] **Step 4: Add the types and helpers to the library**

Append to `src/openflight/iwr6843/firmware_checks.py` (extend the imports at the top to `from dataclasses import dataclass, field` and add `from typing import Callable` and `from openflight.iwr6843.driver import IWR6843Radar`):

```python
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


@dataclass(frozen=True)
class CheckResult:
    """One printed line of the suite."""

    name: str
    status: str
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Context:
    """Everything a check needs; callables are injected so tests never wait."""

    radar: IWR6843Radar
    config: str
    tee_m: float
    level: float | None
    hits: int
    wait_s: float
    shots: int
    profiles: tuple[str, ...]
    prompt: Callable[[str], None]
    sleep: Callable[[float], None]
    clock: Callable[[], float]
    out: Callable[[str], None]


@dataclass(frozen=True)
class Check:
    """A named check; ``needs_swing`` ones run only with ``--swing``."""

    name: str
    run: Callable[[Context], CheckResult]
    needs_swing: bool = False


@dataclass(frozen=True)
class Section:
    """Checks that share a required sensor state: active, stopped, or any."""

    name: str
    sensor: str
    checks: tuple[Check, ...] = field(default_factory=tuple)


def passed(name: str, detail: str = "") -> CheckResult:
    """A PASS line."""
    return CheckResult(name, PASS, detail)


def failed(name: str, detail: str = "") -> CheckResult:
    """A FAIL line."""
    return CheckResult(name, FAIL, detail)


def skipped(name: str, detail: str = "") -> CheckResult:
    """A SKIP line; never fails the run."""
    return CheckResult(name, SKIP, detail)


def stats_snapshot(ctx: Context) -> StatsSnapshot:
    """One ``stats`` round trip."""
    return parse_snapshot(ctx.radar.stats())


def wait_until(
    ctx: Context, predicate: Callable[[], bool], timeout_s: float, poll_s: float = 0.1
) -> bool:
    """Poll ``predicate`` until it is true or ``timeout_s`` passes."""
    deadline = ctx.clock() + timeout_s
    while True:
        if predicate():
            return True
        if ctx.clock() >= deadline:
            return False
        ctx.sleep(poll_s)


def read_port_text(ctx: Context, seconds: float) -> str:
    """Raw CLI text that arrives within ``seconds`` (no command is sent)."""
    deadline = ctx.clock() + seconds
    collected = bytearray()
    while ctx.clock() < deadline:
        waiting = ctx.radar.ser.in_waiting
        chunk = ctx.radar.ser.read(waiting if waiting else 1)
        if chunk:
            collected.extend(chunk)
        else:
            ctx.sleep(0.02)
    return collected.decode(errors="replace")


def ensure_sensor(ctx: Context, state: str) -> None:
    """Bring the sensor to ``state`` ("active", "stopped", "any") if it is not there."""
    if state == "any":
        return
    active = stats_snapshot(ctx).active
    if state == "active" and active != 1:
        ctx.radar.send_config(ctx.config)
    elif state == "stopped" and active != 0:
        ctx.radar.stop_sensor()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -v`
Expected: 12 passed

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff check src/openflight/ tests/ && uv run ruff format --check src/openflight/ && uv run pylint src/openflight/iwr6843/firmware_checks.py --fail-under=9`
Expected: clean

```bash
git add src/openflight/iwr6843/firmware_checks.py tests/iwr6843_fakes.py tests/test_iwr6843_firmware_checks.py
git commit -m "test(iwr6843): add a scripted serial fake and the firmware-check context

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The runner: selection, guards, SKIP, cleanup, JSON, exit code

**Files:**
- Modify: `src/openflight/iwr6843/firmware_checks.py`
- Modify: `tests/test_iwr6843_firmware_checks.py`

**Interfaces:**
- Produces:
  - `run(ctx: Context, sections: tuple[Section, ...], *, only: tuple[str, ...] | None = None, swing: bool = False, fail_fast: bool = False) -> list[CheckResult]`. Raises `ValueError` for an unknown name in `only`. Prints each result through `ctx.out` as it lands, plus one summary line per section.
  - `cleanup(ctx: Context) -> list[CheckResult]` — `triggerCfg 0 0 0`, `debugCfg 0`, `stop_sensor`; each failure is a FAIL named `cleanup/<step>`; never raises.
  - `exit_code(results: list[CheckResult]) -> int` — 1 if any FAIL else 0.
  - `format_result(result: CheckResult) -> str` — `"  PASS  name: detail"` (detail omitted when empty).
  - `write_json(results, path: str | Path) -> None`.
  - `select_sections(sections, only) -> tuple[Section, ...]`.
  - `run_check(ctx, check) -> CheckResult` — one check under the guard (exception → FAIL, `UnsupportedCommand` → SKIP); tests use it when a fake may make the driver raise.

- [ ] **Step 1: Write the failing runner tests**

Append to `tests/test_iwr6843_firmware_checks.py`:

```python
import json

import pytest

from openflight.iwr6843.driver import UnsupportedCommand


def _section(name, sensor="any", *checks):
    return fc.Section(name, sensor, tuple(checks))


def _check(name, result=None, exc=None, needs_swing=False):
    def run(_ctx):
        if exc is not None:
            raise exc
        return result if result is not None else fc.passed(name)

    return fc.Check(name, run, needs_swing)


def _stoppedish_radar():
    return scripted_radar(
        {
            "stats": STATS_STOPPED.encode(),
            "triggerCfg": b"Done\n",
            "debugCfg": b"Done\n",
            "sensorStop": b"Done\n",
        }
    )


def test_run_reports_results_in_catalogue_order_and_prints_them():
    lines: list[str] = []
    sections = (
        _section("a", "any", _check("a/one"), _check("a/two", fc.failed("a/two", "boom"))),
        _section("b", "any", _check("b/one")),
    )

    results = fc.run(_ctx(_stoppedish_radar(), out=lines.append), sections)

    assert [(r.name, r.status) for r in results] == [
        ("a/one", "PASS"),
        ("a/two", "FAIL"),
        ("b/one", "PASS"),
    ]
    assert "  PASS  a/one" in lines
    assert "  FAIL  a/two: boom" in lines
    assert "a: 1 pass, 1 fail, 0 skip" in lines
    assert fc.exit_code(results) == 1


def test_run_only_keeps_catalogue_order_and_rejects_unknown_section_names():
    sections = (_section("a", "any", _check("a/one")), _section("b", "any", _check("b/one")))

    results = fc.run(_ctx(_stoppedish_radar()), sections, only=("b", "a"))
    assert [r.name for r in results] == ["a/one", "b/one"]

    with pytest.raises(ValueError, match="unknown section: zzz"):
        fc.select_sections(sections, ("zzz",))


def test_swing_checks_skip_without_the_flag():
    sections = (_section("t", "any", _check("t/swing", needs_swing=True)),)

    without = fc.run(_ctx(_stoppedish_radar()), sections)
    with_flag = fc.run(_ctx(_stoppedish_radar()), sections, swing=True)

    assert (without[0].status, without[0].detail) == ("SKIP", "needs --swing")
    assert with_flag[0].status == "PASS"
    assert fc.exit_code(without) == 0


def test_unsupported_command_becomes_skip_and_other_exceptions_become_fail():
    sections = (
        _section(
            "a",
            "any",
            _check("a/old", exc=UnsupportedCommand("'l3track' is not recognized")),
            _check("a/broken", exc=RuntimeError("wedged")),
            _check("a/after"),
        ),
    )

    results = fc.run(_ctx(_stoppedish_radar()), sections)

    assert results[0].status == "SKIP" and "older firmware" in results[0].detail
    assert results[1].status == "FAIL" and "wedged" in results[1].detail
    assert results[2].status == "PASS"


def test_fail_fast_stops_after_the_first_fail():
    sections = (_section("a", "any", _check("a/bad", fc.failed("a/bad")), _check("a/never")),)

    results = fc.run(_ctx(_stoppedish_radar()), sections, fail_fast=True)

    assert [r.name for r in results] == ["a/bad"]


def test_sections_reconcile_sensor_state_before_running(monkeypatch):
    calls: list[str] = []
    radar = _stoppedish_radar()
    monkeypatch.setattr(radar, "send_config", lambda cfg: calls.append("start"))
    sections = (_section("needs-active", "active", _check("needs-active/x")),)

    fc.run(_ctx(radar), sections)

    assert calls == ["start"]


def test_reconciliation_failure_fails_every_check_in_the_section(monkeypatch):
    radar = _stoppedish_radar()

    def explode(_cfg):
        raise RuntimeError("did not enter active capture mode")

    monkeypatch.setattr(radar, "send_config", explode)
    sections = (_section("s", "active", _check("s/one"), _check("s/two")),)

    results = fc.run(_ctx(radar), sections)

    assert [r.status for r in results] == ["FAIL", "FAIL"]
    assert "did not enter active" in results[1].detail


def test_cleanup_runs_after_a_raising_check(monkeypatch):
    radar = _stoppedish_radar()
    stopped: list[bool] = []
    monkeypatch.setattr(radar, "stop_sensor", lambda: stopped.append(True))

    results = fc.cleanup(_ctx(radar))

    assert radar.ser.written[:2] == ["triggerCfg 0 0 0", "debugCfg 0"]
    assert stopped == [True]
    assert [r.status for r in results] == ["PASS", "PASS", "PASS"]


def test_cleanup_failure_forces_exit_1(monkeypatch):
    radar = _stoppedish_radar()

    def explode():
        raise RuntimeError("remained active")

    monkeypatch.setattr(radar, "stop_sensor", explode)

    results = fc.cleanup(_ctx(radar))

    assert results[-1] == fc.CheckResult("cleanup/sensorStop", "FAIL", "remained active", results[-1].seconds)
    assert fc.exit_code(results) == 1


def test_write_json_records_name_status_detail_seconds(tmp_path):
    path = tmp_path / "out.json"

    fc.write_json([fc.passed("a/one", "ok"), fc.skipped("b/two", "why")], path)

    assert json.loads(path.read_text()) == [
        {"name": "a/one", "status": "PASS", "detail": "ok", "seconds": 0.0},
        {"name": "b/two", "status": "SKIP", "detail": "why", "seconds": 0.0},
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k "run_ or swing_checks or unsupported or fail_fast or reconcil or cleanup or write_json" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'run'`

- [ ] **Step 3: Implement the runner**

Append to `src/openflight/iwr6843/firmware_checks.py` (add `import json`, `from dataclasses import asdict`, `from pathlib import Path`, and `from openflight.iwr6843.driver import IWR6843Radar, UnsupportedCommand` at the top):

```python
def format_result(result: CheckResult) -> str:
    """``  PASS  name: detail`` — the line the operator reads."""
    suffix = f": {result.detail}" if result.detail else ""
    return f"  {result.status}  {result.name}{suffix}"


def select_sections(sections: tuple[Section, ...], only: tuple[str, ...] | None) -> tuple[Section, ...]:
    """Sections named in ``only`` in catalogue order; all of them when ``only`` is None."""
    if only is None:
        return sections
    known = {section.name for section in sections}
    for name in only:
        if name not in known:
            raise ValueError(f"unknown section: {name} (choose from {', '.join(sorted(known))})")
    wanted = set(only)
    return tuple(section for section in sections if section.name in wanted)


def run_check(ctx: Context, check: Check) -> CheckResult:
    """Run one check under the suite's guard: exceptions become FAIL, unsupported commands SKIP."""
    started = ctx.clock()
    try:
        result = check.run(ctx)
    except UnsupportedCommand as exc:
        result = skipped(check.name, f"older firmware: {exc}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        result = failed(check.name, f"{type(exc).__name__}: {exc}")
    return CheckResult(result.name, result.status, result.detail, ctx.clock() - started)


def _emit(ctx: Context, results: list[CheckResult], result: CheckResult) -> None:
    results.append(result)
    ctx.out(format_result(result))


def _summarise(ctx: Context, section: Section, results: list[CheckResult]) -> None:
    names = {check.name for check in section.checks}
    mine = [r for r in results if r.name in names]
    counts = {status: sum(1 for r in mine if r.status == status) for status in (PASS, FAIL, SKIP)}
    ctx.out(f"{section.name}: {counts[PASS]} pass, {counts[FAIL]} fail, {counts[SKIP]} skip")


def run(
    ctx: Context,
    sections: tuple[Section, ...],
    *,
    only: tuple[str, ...] | None = None,
    swing: bool = False,
    fail_fast: bool = False,
) -> list[CheckResult]:
    """Run the selected sections in catalogue order and return every result."""
    results: list[CheckResult] = []
    for section in select_sections(sections, only):
        try:
            ensure_sensor(ctx, section.sensor)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            for check in section.checks:
                _emit(ctx, results, failed(check.name, f"sensor not {section.sensor}: {exc}"))
            _summarise(ctx, section, results)
            if fail_fast:
                return results
            continue
        for check in section.checks:
            if check.needs_swing and not swing:
                _emit(ctx, results, skipped(check.name, "needs --swing"))
                continue
            _emit(ctx, results, run_check(ctx, check))
            if fail_fast and results[-1].status == FAIL:
                _summarise(ctx, section, results)
                return results
        _summarise(ctx, section, results)
    return results


def cleanup(ctx: Context) -> list[CheckResult]:
    """Leave the radar disarmed, quiet and stopped; report each step, never raise."""
    steps: tuple[tuple[str, Callable[[], None]], ...] = (
        ("cleanup/triggerCfg off", lambda: ctx.radar.cmd("triggerCfg 0 0 0", 2.0)),
        ("cleanup/debugCfg off", lambda: ctx.radar.cmd("debugCfg 0", 2.0)),
        ("cleanup/sensorStop", ctx.radar.stop_sensor),
    )
    results: list[CheckResult] = []
    for name, step in steps:
        started = ctx.clock()
        try:
            step()
            result = passed(name)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            result = failed(name, str(exc))
        _emit(ctx, results, CheckResult(result.name, result.status, result.detail, ctx.clock() - started))
    return results


def exit_code(results: list[CheckResult]) -> int:
    """1 when any check failed, else 0. SKIP never fails the run."""
    return 1 if any(r.status == FAIL for r in results) else 0


def write_json(results: list[CheckResult], path: str | Path) -> None:
    """Persist results as a list of {name, status, detail, seconds}."""
    Path(path).write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -v`
Expected: all passed

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src/openflight/ tests/ && uv run ruff format --check src/openflight/ && uv run pylint src/openflight/iwr6843/firmware_checks.py --fail-under=9`

```bash
git add src/openflight/iwr6843/firmware_checks.py tests/test_iwr6843_firmware_checks.py
git commit -m "feat(iwr6843): add the firmware check runner with guards and cleanup

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: `lifecycle` section

**Files:**
- Modify: `src/openflight/iwr6843/firmware_checks.py`
- Modify: `tests/test_iwr6843_firmware_checks.py`

**Interfaces:**
- Consumes: `Context`, `stats_snapshot`, `wait_until`, `passed/failed`.
- Produces: `lifecycle_section() -> Section` named `"lifecycle"`, sensor `"active"`, five checks named exactly `lifecycle/config accepted`, `lifecycle/frames advance`, `lifecycle/config commands refused while active`, `lifecycle/sensorStop idles the sensor`, `lifecycle/restart resets counters`.
- Note on ordering: check 4 stops the sensor and check 5 restarts it, so the section ends active for the next section.

- [ ] **Step 1: Write the failing lifecycle tests**

Append to `tests/test_iwr6843_firmware_checks.py`:

```python
def _stats_counting(active=1, start=1000, step=500, phase="off", enabled=0, latched=0):
    def reply(n):
        return (
            f"frames={start + n * step} wraps=0 active={active} calib=0x0 rf_faults=0 "
            "hwa_frames=1 hwa_out=1 hwa_rearms=1 hwa_rearm_err=0 hwa_missed=0 "
            "freeze_req=0 freeze_done=0 freeze_to=0 format=iq16 plan=16pre/8post "
            "loops=12 used=100/200\n"
            "iq8_packed=0 iq8_overrun=0 iq8_clipped=0 pending=0 pre_seen=999 post_kept=0 "
            "post_seen=0 stride=1\n"
            f"trig phase={phase} tee=0 latched={latched} enabled={enabled}\n"
            "detect dropped=0 stale=0\nrearm_last_us=1 rearm_max_us=2 rearm_timed=3\nDone\n"
        ).encode()

    return reply


def _lifecycle_radar(**overrides):
    replies = {
        "stats": _stats_counting(),
        "captureCfg": b"Error: stop the sensor before captureCfg\n",
        "phaseCaptureCfg": b"Error: stop the sensor before phaseCaptureCfg\n",
        "captureFormat": b"Error: stop the sensor before captureFormat\n",
        "iq8Scale": b"Error: stop the sensor before iq8Scale\n",
        "sensorStop": b"Done\n",
    }
    replies.update(overrides)
    return scripted_radar(replies)


def _names(section):
    return [check.name for check in section.checks]


def test_lifecycle_section_names_match_the_spec():
    assert _names(fc.lifecycle_section()) == [
        "lifecycle/config accepted",
        "lifecycle/frames advance",
        "lifecycle/config commands refused while active",
        "lifecycle/sensorStop idles the sensor",
        "lifecycle/restart resets counters",
    ]


def test_lifecycle_config_accepted_reads_active_and_faults(monkeypatch):
    radar = _lifecycle_radar()
    monkeypatch.setattr(radar, "send_config", lambda cfg: None)
    check = fc.lifecycle_section().checks[0]

    assert check.run(_ctx(radar)).status == "PASS"

    faulty = _lifecycle_radar(stats=lambda n: b"frames=1 active=1 rf_faults=3\nDone\n")
    monkeypatch.setattr(faulty, "send_config", lambda cfg: None)
    result = check.run(_ctx(faulty))
    assert result.status == "FAIL" and "rf_faults=3" in result.detail


def test_lifecycle_frames_advance_fails_when_the_counter_is_stuck():
    moving = fc.lifecycle_section().checks[1].run(_ctx(_lifecycle_radar()))
    stuck = fc.lifecycle_section().checks[1].run(_ctx(_lifecycle_radar(stats=_stats_counting(step=0))))

    assert moving.status == "PASS"
    assert stuck.status == "FAIL"


def test_lifecycle_config_commands_must_be_refused_while_active():
    check = fc.lifecycle_section().checks[2]

    assert check.run(_ctx(_lifecycle_radar())).status == "PASS"

    leaky = _lifecycle_radar(captureFormat=b"Capture format: iq16\nDone\n")
    result = check.run(_ctx(leaky))
    assert result.status == "FAIL" and "captureFormat" in result.detail


def test_lifecycle_sensor_stop_requires_active_zero(monkeypatch):
    check = fc.lifecycle_section().checks[3]
    radar = _lifecycle_radar(stats=_stats_counting(active=0))
    monkeypatch.setattr(radar, "stop_sensor", lambda: None)
    assert check.run(_ctx(radar)).status == "PASS"

    still = _lifecycle_radar()

    def refuse():
        raise RuntimeError("IWR6843 remained active after sensorStop")

    monkeypatch.setattr(still, "stop_sensor", refuse)
    result = check.run(_ctx(still))
    assert result.status == "FAIL" and "remained active" in result.detail


def test_lifecycle_restart_resets_counters(monkeypatch):
    check = fc.lifecycle_section().checks[4]
    # First stats: high frame count from the old session; after send_config the count restarts low.
    radar = _lifecycle_radar(stats=lambda n: _stats_counting(start=50000 if n == 0 else 10)(0))
    monkeypatch.setattr(radar, "send_config", lambda cfg: None)
    assert check.run(_ctx(radar)).status == "PASS"

    same = _lifecycle_radar(stats=_stats_counting(start=50000, step=100))
    monkeypatch.setattr(same, "send_config", lambda cfg: None)
    assert check.run(_ctx(same)).status == "FAIL"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k lifecycle -v`
Expected: FAIL with `AttributeError: ... 'lifecycle_section'`

- [ ] **Step 3: Implement the lifecycle section**

Append to `src/openflight/iwr6843/firmware_checks.py`:

```python
_CONFIG_WHILE_ACTIVE = (
    "captureCfg 20 53 32 53 47 8",
    "phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1",
    "captureFormat iq16",
    "iq8Scale 64",
)


def _check_config_accepted(ctx: Context) -> CheckResult:
    name = "lifecycle/config accepted"
    ctx.radar.send_config(ctx.config)
    snap = stats_snapshot(ctx)
    faults = parse_stats(snap.raw).get("rf_faults")
    if snap.active != 1 or faults != 0:
        return failed(name, f"active={snap.active} rf_faults={faults}")
    return passed(name, f"active=1 rf_faults=0 format={snap.format} plan={snap.plan_pre}pre/{snap.plan_post}post")


def _check_frames_advance(ctx: Context) -> CheckResult:
    name = "lifecycle/frames advance"
    before = stats_snapshot(ctx).frames
    ctx.sleep(0.5)
    after = stats_snapshot(ctx).frames
    if before is None or after is None or after <= before:
        return failed(name, f"frames {before} -> {after} over 0.5 s")
    return passed(name, f"frames {before} -> {after} over 0.5 s")


def _check_config_refused_while_active(ctx: Context) -> CheckResult:
    name = "lifecycle/config commands refused while active"
    leaked = []
    for line in _CONFIG_WHILE_ACTIVE:
        reply = ctx.radar.cmd(line, 2.0)
        if "Error" not in reply or "stop the sensor" not in reply:
            leaked.append(f"{line.split()[0]}: {reply.strip()[:60]!r}")
    if leaked:
        return failed(name, "; ".join(leaked))
    return passed(name, f"{len(_CONFIG_WHILE_ACTIVE)} commands refused")


def _check_sensor_stop(ctx: Context) -> CheckResult:
    name = "lifecycle/sensorStop idles the sensor"
    ctx.radar.stop_sensor()
    snap = stats_snapshot(ctx)
    if snap.active != 0:
        return failed(name, f"active={snap.active} after sensorStop")
    return passed(name, "active=0")


def _check_restart_resets(ctx: Context) -> CheckResult:
    name = "lifecycle/restart resets counters"
    before = stats_snapshot(ctx).frames
    ctx.radar.send_config(ctx.config)
    after = stats_snapshot(ctx)
    if after.active != 1 or before is None or after.frames is None or after.frames >= before:
        return failed(name, f"active={after.active} frames {before} -> {after.frames}")
    return passed(name, f"frames {before} -> {after.frames}, latched={after.latched}")


def lifecycle_section() -> Section:
    """sensorStart/sensorStop/stats behave; the section ends with the sensor active."""
    return Section(
        "lifecycle",
        "active",
        (
            Check("lifecycle/config accepted", _check_config_accepted),
            Check("lifecycle/frames advance", _check_frames_advance),
            Check("lifecycle/config commands refused while active", _check_config_refused_while_active),
            Check("lifecycle/sensorStop idles the sensor", _check_sensor_stop),
            Check("lifecycle/restart resets counters", _check_restart_resets),
        ),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k lifecycle -v`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src/openflight/ tests/ && uv run ruff format --check src/openflight/ && uv run pylint src/openflight/iwr6843/firmware_checks.py --fail-under=9`

```bash
git add src/openflight/iwr6843/firmware_checks.py tests/test_iwr6843_firmware_checks.py
git commit -m "feat(iwr6843): add the lifecycle firmware checks

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `profiles` section

**Files:**
- Modify: `src/openflight/iwr6843/firmware_checks.py`
- Modify: `tests/test_iwr6843_firmware_checks.py`

**Interfaces:**
- Produces: `profiles_section(profiles: tuple[str, ...]) -> Section` named `"profiles"`, sensor `"stopped"`, checks `profiles/captureCfg validation`, `profiles/phaseCaptureCfg validation`, `profiles/captureFormat`, `profiles/iq8Scale`, then one `profiles/profile <basename> loads` per path in `profiles`.
- Produces: `expected_profile_shape(cfg_path) -> tuple[str, int]` — `(format, stride)` declared by the cfg's `captureFormat` and `captureCfg`/`phaseCaptureCfg` lines (`captureCfg` stride is the optional 7th value, default 2 for iq16 builds is not assumed: a 6-value `captureCfg` returns stride `None` and the check then only compares format).
- Validation tables are module constants so the tests and the source-pinning test in Task 9 can read them: `CAPTURE_CFG_CASES`, `PHASE_CAPTURE_CFG_CASES`, `CAPTURE_FORMAT_CASES`, `IQ8_SCALE_CASES`, each a tuple of `(line, expect_ok: bool)`.

- [ ] **Step 1: Write the failing profile tests**

Append to `tests/test_iwr6843_firmware_checks.py`:

```python
def _validating(prefix, ok_reply):
    """Reply Error for lines the firmware would refuse, ok_reply otherwise (table-driven)."""
    table = {
        "captureCfg": fc.CAPTURE_CFG_CASES,
        "phaseCaptureCfg": fc.PHASE_CAPTURE_CFG_CASES,
        "captureFormat": fc.CAPTURE_FORMAT_CASES,
        "iq8Scale": fc.IQ8_SCALE_CASES,
    }[prefix]
    expected = dict(table)

    class Port(ScriptedSerial):
        def write(self, data):
            line = data.decode().strip()
            self.written.append(line)
            if line.startswith(prefix):
                good = expected.get(line, False)
                self.inject(ok_reply(line) if good else f"Error: {prefix} rejected\n".encode())
            else:
                self.inject(b"Done\n")

    from openflight.iwr6843.driver import IWR6843Radar

    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = Port({})
    radar.port = "scripted"
    radar._trigger_pending = b""
    return radar


def test_profiles_section_names_match_the_spec():
    section = fc.profiles_section(("config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",))

    assert _names(section) == [
        "profiles/captureCfg validation",
        "profiles/phaseCaptureCfg validation",
        "profiles/captureFormat",
        "profiles/iq8Scale",
        "profiles/profile iwr6843_l3dump_wide_24f3ms_53bin_iq16 loads",
    ]
    assert section.sensor == "stopped"


def test_capture_cfg_cases_cover_the_spec_table():
    lines = dict(fc.CAPTURE_CFG_CASES)
    assert lines["captureCfg 20 53 32 53 47 8"] is True
    assert lines["captureCfg 20 53 32 53 47 8 2"] is True
    assert lines["captureCfg 20 53 32 53 47"] is False          # wrong count
    assert lines["captureCfg x 53 32 53 47 8"] is False         # non-integer
    assert lines["captureCfg 300 53 32 53 47 8"] is False       # above 255
    assert lines["captureCfg 20 0 32 53 47 8"] is False         # zero pre bins
    assert lines["captureCfg 100 53 32 53 47 8"] is False       # window past 128
    assert lines["captureCfg 20 53 32 53 47 64"] is False       # post frames at the cap


def test_capture_cfg_validation_passes_and_fails_by_table():
    check = fc.profiles_section(()).checks[0]

    good = check.run(_ctx(_validating("captureCfg", lambda _l: b"Done\n")))
    assert good.status == "PASS"

    lax = scripted_radar({"captureCfg": b"Done\n"})  # accepts every line, even the bad ones
    result = check.run(_ctx(lax))
    assert result.status == "FAIL" and "accepted" in result.detail


def test_capture_format_and_iq8_scale_echo_their_values():
    fmt = fc.profiles_section(()).checks[2]
    scale = fc.profiles_section(()).checks[3]

    fmt_radar = _validating("captureFormat", lambda line: f"Capture format: {line.split()[1]}\nDone\n".encode())
    scale_radar = _validating("iq8Scale", lambda line: f"IQ8 fixed scale: {line.split()[1]} (HWA shift 6)\nDone\n".encode())

    assert fmt.run(_ctx(fmt_radar)).status == "PASS"
    assert scale.run(_ctx(scale_radar)).status == "PASS"

    silent = _validating("captureFormat", lambda _l: b"Done\n")
    result = fmt.run(_ctx(silent))
    assert result.status == "FAIL" and "echo" in result.detail


def test_expected_profile_shape_reads_the_cfg(tmp_path):
    cfg = tmp_path / "p.cfg"
    cfg.write_text("captureFormat iq8\niq8Scale 128\nphaseCaptureCfg 20 53 8 32 53 10 47 53 64 27 1\nsensorStart\n")
    plain = tmp_path / "q.cfg"
    plain.write_text("captureFormat iq16\ncaptureCfg 20 53 32 53 47 8\nsensorStart\n")

    assert fc.expected_profile_shape(cfg) == ("iq8", 1)
    assert fc.expected_profile_shape(plain) == ("iq16", None)


def test_profile_load_check_compares_format_stride_and_capacity(tmp_path, monkeypatch):
    cfg = tmp_path / "wide.cfg"
    cfg.write_text("captureFormat iq16\nphaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1\nsensorStart\n")
    check = fc.profiles_section((str(cfg),)).checks[-1]

    def radar_with(fmt, stride, used, cap):
        radar = scripted_radar(
            {"stats": f"frames=9 active=1 format={fmt} plan=16pre/8post used={used}/{cap}\nstride={stride}\nDone\n".encode()}
        )
        monkeypatch.setattr(radar, "send_config", lambda p: None)
        monkeypatch.setattr(radar, "stop_sensor", lambda: None)
        return radar

    assert check.run(_ctx(radar_with("iq16", 1, 100, 200))).status == "PASS"
    assert check.run(_ctx(radar_with("iq8", 1, 100, 200))).status == "FAIL"
    assert check.run(_ctx(radar_with("iq16", 2, 100, 200))).status == "FAIL"
    assert check.run(_ctx(radar_with("iq16", 1, 300, 200))).status == "FAIL"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k "profile or capture_cfg or capture_format" -v`
Expected: FAIL with `AttributeError: ... 'profiles_section'`

- [ ] **Step 3: Implement the profiles section**

Append to `src/openflight/iwr6843/firmware_checks.py`:

```python
# (line, accepted?) — the firmware rules are in l3_cli_captureCfg / l3_cli_phaseCaptureCfg /
# l3_cli_captureFormat / l3_cli_iq8Scale (firmware/iwr6843/l3_dump.c). N_SAMPLES is 128,
# L3_MAX_CAPTURE_FRAMES is 64, L3_MAX_POST_STRIDE is 16.
CAPTURE_CFG_CASES: tuple[tuple[str, bool], ...] = (
    ("captureCfg 20 53 32 53 47 8", True),
    ("captureCfg 20 53 32 53 47 8 2", True),
    ("captureCfg 20 53 32 53 47", False),
    ("captureCfg x 53 32 53 47 8", False),
    ("captureCfg 300 53 32 53 47 8", False),
    ("captureCfg 20 0 32 53 47 8", False),
    ("captureCfg 100 53 32 53 47 8", False),
    ("captureCfg 20 53 32 53 47 64", False),
    ("captureCfg 20 53 32 53 47 8 17", False),
)
PHASE_CAPTURE_CFG_CASES: tuple[tuple[str, bool], ...] = (
    ("phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 1", True),
    ("phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8", False),
    ("phaseCaptureCfg 20 53 x 32 53 7 47 53 47 8 1", False),
    ("phaseCaptureCfg 20 53 0 32 53 7 47 53 47 8 1", False),
    ("phaseCaptureCfg 20 53 9 32 53 30 47 53 47 34 1", False),
    ("phaseCaptureCfg 20 53 9 32 53 7 100 53 47 8 1", False),
    ("phaseCaptureCfg 20 53 9 32 53 7 47 53 47 8 17", False),
)
CAPTURE_FORMAT_CASES: tuple[tuple[str, bool], ...] = (
    ("captureFormat iq16", True),
    ("captureFormat iq8", True),
    ("captureFormat iq32", False),
    ("captureFormat", False),
)
IQ8_SCALE_CASES: tuple[tuple[str, bool], ...] = (
    ("iq8Scale 16", True),
    ("iq8Scale 32", True),
    ("iq8Scale 64", True),
    ("iq8Scale 128", True),
    ("iq8Scale 256", True),
    ("iq8Scale 8", False),
    ("iq8Scale 512", False),
    ("iq8Scale 48", False),
    ("iq8Scale abc", False),
)


def _run_validation_table(
    ctx: Context, name: str, cases: tuple[tuple[str, bool], ...], echo: str | None = None
) -> CheckResult:
    """Send each line; accepted ones must not Error (and must ``echo``), refused ones must."""
    wrong: list[str] = []
    for line, expect_ok in cases:
        reply = ctx.radar.cmd(line, 2.0)
        refused = "Error" in reply
        if expect_ok and refused:
            wrong.append(f"refused {line!r}")
        elif not expect_ok and not refused:
            wrong.append(f"accepted {line!r}")
        elif expect_ok and echo is not None and echo not in reply:
            wrong.append(f"no {echo!r} echo for {line!r}")
    if wrong:
        return failed(name, "; ".join(wrong))
    return passed(name, f"{len(cases)} lines behaved")


def _check_capture_cfg(ctx: Context) -> CheckResult:
    return _run_validation_table(ctx, "profiles/captureCfg validation", CAPTURE_CFG_CASES)


def _check_phase_capture_cfg(ctx: Context) -> CheckResult:
    return _run_validation_table(ctx, "profiles/phaseCaptureCfg validation", PHASE_CAPTURE_CFG_CASES)


def _check_capture_format(ctx: Context) -> CheckResult:
    return _run_validation_table(ctx, "profiles/captureFormat", CAPTURE_FORMAT_CASES, echo="Capture format:")


def _check_iq8_scale(ctx: Context) -> CheckResult:
    return _run_validation_table(ctx, "profiles/iq8Scale", IQ8_SCALE_CASES, echo="IQ8 fixed scale:")


def expected_profile_shape(cfg_path: str | Path) -> tuple[str | None, int | None]:
    """``(format, stride)`` a .cfg declares; stride None for a 6-value captureCfg."""
    fmt: str | None = None
    stride: int | None = None
    for raw_line in Path(cfg_path).read_text(encoding="utf-8").splitlines():
        fields = raw_line.split()
        if not fields:
            continue
        if fields[0] == "captureFormat" and len(fields) > 1:
            fmt = fields[1]
        elif fields[0] == "phaseCaptureCfg" and len(fields) == 12:
            stride = int(fields[11])
        elif fields[0] == "captureCfg" and len(fields) == 8:
            stride = int(fields[7])
    return fmt, stride


def _profile_check(cfg_path: str) -> Check:
    stem = Path(cfg_path).stem
    name = f"profiles/profile {stem} loads"

    def run(ctx: Context) -> CheckResult:
        want_fmt, want_stride = expected_profile_shape(cfg_path)
        ctx.radar.send_config(cfg_path)
        try:
            snap = stats_snapshot(ctx)
        finally:
            ctx.radar.stop_sensor()
        problems = []
        if snap.active != 1:
            problems.append(f"active={snap.active}")
        if want_fmt is not None and snap.format != want_fmt:
            problems.append(f"format={snap.format} want {want_fmt}")
        if want_stride is not None and snap.stride != want_stride:
            problems.append(f"stride={snap.stride} want {want_stride}")
        if snap.used is not None and snap.capacity is not None and snap.used > snap.capacity:
            problems.append(f"used={snap.used} > capacity={snap.capacity}")
        if problems:
            return failed(name, "; ".join(problems))
        return passed(name, f"format={snap.format} plan={snap.plan_pre}pre/{snap.plan_post}post used={snap.used}/{snap.capacity}")

    return Check(name, run)


def profiles_section(profiles: tuple[str, ...]) -> Section:
    """Argument validation of the capture commands, then each shipped profile loads."""
    checks = [
        Check("profiles/captureCfg validation", _check_capture_cfg),
        Check("profiles/phaseCaptureCfg validation", _check_phase_capture_cfg),
        Check("profiles/captureFormat", _check_capture_format),
        Check("profiles/iq8Scale", _check_iq8_scale),
    ]
    checks.extend(_profile_check(path) for path in profiles)
    return Section("profiles", "stopped", tuple(checks))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k "profile or capture_cfg or capture_format" -v`
Expected: all passed

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src/openflight/ tests/ && uv run ruff format --check src/openflight/ && uv run pylint src/openflight/iwr6843/firmware_checks.py --fail-under=9`

```bash
git add src/openflight/iwr6843/firmware_checks.py tests/test_iwr6843_firmware_checks.py
git commit -m "feat(iwr6843): add the capture-profile firmware checks

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `readback` section (absorbs `test_iwr_self_trigger.py`)

**Files:**
- Modify: `src/openflight/iwr6843/firmware_checks.py`
- Modify: `tests/test_iwr6843_firmware_checks.py`
- Delete: `scripts/hardware-test/test_iwr_self_trigger.py`

**Interfaces:**
- Consumes: `IWR6843Radar.read_dump`, `read_sparse`, `read_tracked`, `_read_packet`; `openflight.iwr6843.dump.parse_header`; `openflight.iwr6843.sparse` (`POWER_MAGIC`, `SPARSE_REQUEST_MAX_BYTES`, `SparsePlan`, `fit_cell_request`, `format_cell_request`, `parse_power`, `power_packet_size`); `openflight.iwr6843.monitor.read_capture_config`; `openflight.iwr6843.tracking.LOOP_PRI_S`, `RANGE_SPAN_M`; `openflight.iwr6843.sparse.RANGE_FFT_SIZE`; `tests.iwr6843_fakes.power_packet`, `slice_packet`, `vertical_loop_power`.
- Produces: `readback_section() -> Section` named `"readback"`, sensor `"active"`, checks in this order: `readback/l3dump streams a valid dump`, `readback/l3sparse returns every cell at the limit`, `readback/l3sparse refuses an oversized request`, `readback/l3sparse refuses a late request, then works`, `readback/l3track without trackCfg is refused`, `readback/trackCfg validation`, `readback/l3track streams the tracked cells`.
- Produces: `track_config_command(cfg_path) -> str` — `"trackCfg <loopPeriodS> <rangeResM> 0 0 0"` with 17 significant digits, loop period from `read_capture_config(cfg).loop_period_s` or `LOOP_PRI_S`, resolution `RANGE_SPAN_M / RANGE_FFT_SIZE`.
- Produces: `TRACK_CFG_CASES` tuple of `(line, accepted)`.
- Produces: `SPARSE_REQUEST_TIMEOUT_S = 5.0` (mirrors `L3_SPARSE_REQUEST_TIMEOUT_MS` in `firmware/iwr6843/dump_format.h`).
- Note: `l3track` is only reachable on IQ16 storage; `read_tracked` returns `None` on IQ8, which the check reports as SKIP with "IQ8 profile: l3track not available".

- [ ] **Step 1: Write the failing readback tests**

Append to `tests/test_iwr6843_firmware_checks.py`:

```python
import numpy as np

from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.sparse import OnboardTrack, PowerSummary
from openflight.iwr6843.tracking import Geometry
from tests.iwr6843_fakes import power_packet, slice_packet, vertical_loop_power

WIDE_CFG = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"


def _cube(frames=4, loops=2, n_tx=3, n_rx=4, bins=6):
    rng = np.random.default_rng(1)
    shape = (frames, loops * n_tx, n_rx, bins)
    return rng.normal(size=shape) + 1j * rng.normal(size=shape)


def _summary(cube, n_tx=3):
    return vertical_loop_power(cube, n_tx=n_tx)


def _dump_bytes(cube, n_tx=3):
    return pack_dump(cube, n_tx=n_tx, version=3)


def _stats_for(cube, n_tx=3, freeze=(1, 1), fmt="iq16"):
    frames, chirps = cube.shape[0], cube.shape[1]

    def reply(_n):
        return (
            f"frames=100 active=1 rf_faults=0 freeze_req={freeze[0]} freeze_done={freeze[1]} "
            f"format={fmt} plan={frames - 1}pre/1post loops={chirps // n_tx} used=1/2\n"
            "stride=1\ntrig phase=off tee=0 latched=0 enabled=0\nDone\n"
        ).encode()

    return reply


def test_readback_section_names_match_the_spec():
    assert _names(fc.readback_section()) == [
        "readback/l3dump streams a valid dump",
        "readback/l3sparse returns every cell at the limit",
        "readback/l3sparse refuses an oversized request",
        "readback/l3sparse refuses a late request, then works",
        "readback/l3track without trackCfg is refused",
        "readback/trackCfg validation",
        "readback/l3track streams the tracked cells",
    ]


def test_l3dump_check_validates_the_header_against_the_plan():
    cube = _cube()
    good = scripted_radar({"l3dump": _dump_bytes(cube) + b"Done\n", "stats": _stats_for(cube)})
    check = fc.readback_section().checks[0]

    assert check.run(_ctx(good)).status == "PASS"

    wrong_plan = scripted_radar({"l3dump": _dump_bytes(cube) + b"Done\n", "stats": _stats_for(_cube(frames=9))})
    result = check.run(_ctx(wrong_plan))
    assert result.status == "FAIL" and "n_frames" in result.detail


def _sparse_radar(cube, *, after_request=None, trailer=b"Done\n", late_first=False):
    """Plays l3sparse exchanges: ILP1 power, then the cells the host asks for.

    ``after_request`` replaces the ILS1 reply (to script a refusal).
    ``late_first`` makes the first l3sparse time out with the firmware's
    "request missing" error instead of waiting for cells.
    """
    from tests.iwr6843_fakes import parse_cell_request

    summary = _summary(cube)
    stats = _stats_for(cube)
    state = {"sparse": 0}

    class Port(ScriptedSerial):
        def write(self, data):
            line = data.decode(errors="replace").strip()
            self.written.append(line)
            if line == "l3sparse":
                state["sparse"] += 1
                self.inject(b"l3sparse\n" + power_packet(summary))
                if late_first and state["sparse"] == 1:
                    self.inject(b"Error: sparse cell request missing\nDone\n")
            elif line.startswith("cells"):
                if after_request is not None:
                    self.inject(after_request)
                else:
                    self.inject(slice_packet(cube, 3, parse_cell_request(data)) + trailer)
            elif line == "stats":
                self.inject(stats(0))
            else:
                self.inject(b"Done\n")

    from openflight.iwr6843.driver import IWR6843Radar

    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = Port({})
    radar.port = "scripted"
    radar._trigger_pending = b""
    return radar


def test_l3sparse_limit_check_requires_every_cell_and_a_noise_floor():
    check = fc.readback_section().checks[1]

    assert check.run(_ctx(_sparse_radar(_cube()))).status == "PASS"

    empty = _sparse_radar(_cube(), after_request=b"ILS1\x00\x00Done\n")
    assert fc.run_check(_ctx(empty), check).status == "FAIL"  # driver may raise on missing cells


def test_l3sparse_oversized_check_expects_the_firmware_refusal():
    check = fc.readback_section().checks[2]
    refusing = _sparse_radar(_cube(frames=8, bins=40), after_request=b"Error: sparse cell request longer than L3_SPARSE_REQUEST_MAX\nDone\n")

    assert check.run(_ctx(refusing)).status == "PASS"

    lenient = _sparse_radar(_cube(frames=8, bins=40), after_request=b"Done\n")
    assert check.run(_ctx(lenient)).status == "FAIL"


def test_l3sparse_late_check_waits_out_the_timeout_then_reads_again():
    slept: list[float] = []
    radar = _sparse_radar(_cube(), late_first=True)
    check = fc.readback_section().checks[3]

    result = check.run(_ctx(radar, sleep=slept.append))

    assert result.status == "PASS", result.detail
    assert any(s >= fc.SPARSE_REQUEST_TIMEOUT_S for s in slept)
    assert radar.ser.written.count("l3sparse") == 2


def test_l3track_without_trackcfg_must_be_refused():
    cube = _cube()
    check = fc.readback_section().checks[4]
    refusing = scripted_radar({"l3track": b"Error: l3track needs trackCfg\n", "stats": _stats_for(cube)})
    assert check.run(_ctx(refusing)).status == "PASS"

    # Wrong error text AND freeze_req climbing on every stats call: the ring froze.
    freezing = scripted_radar(
        {
            "l3track": b"Error: something else\n",
            "stats": lambda n: _stats_for(cube, freeze=(1 + n, 1 + n))(0),
        }
    )
    result = check.run(_ctx(freezing))
    assert result.status == "FAIL" and "freeze_req moved" in result.detail


def test_track_cfg_cases_and_command_builder():
    lines = dict(fc.TRACK_CFG_CASES)
    assert lines["trackCfg 9e-05 0.046875 0 0 0"] is True
    assert lines["trackCfg 9e-05 0.046875 0 0"] is False
    assert lines["trackCfg -1 0.046875 0 0 0"] is False
    assert lines["trackCfg 0 0.046875 0 0 0"] is False
    assert lines["trackCfg 9e-05 0 0 0 0"] is False

    command = fc.track_config_command(WIDE_CFG)
    assert command.startswith("trackCfg ")
    fields = command.split()[1:]
    assert len(fields) == 5 and float(fields[1]) == 6.0 / 128 and fields[2:] == ["0", "0", "0"]


def test_l3track_streams_and_rearms():
    cube = _cube()
    summary = _summary(cube)
    track = OnboardTrack(True, 3, 1.0, 2.0, 0.1, 0.0, 0.01)
    packet = summary.header_bytes(b"ILT1") + track.to_bytes() + slice_packet(cube, 3, [(0, 1), (1, 2)]) + b"Done\n"
    radar = scripted_radar(
        {
            "trackCfg": b"Done\n",
            "l3track": packet,
            "stats": lambda n: _stats_for(cube, freeze=(1 + (n > 0), 1 + (n > 0)))(0),
        }
    )
    check = fc.readback_section().checks[6]

    result = check.run(_ctx(radar))

    assert result.status == "PASS", result.detail
    assert "found=True" in result.detail

    iq8 = scripted_radar({"trackCfg": b"Done\n", "l3track": b"Error: l3track needs IQ16\n", "stats": _stats_for(cube, fmt="iq8")})
    assert check.run(_ctx(iq8)).status == "SKIP"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k "readback or l3dump or l3sparse or l3track or track_cfg" -v`
Expected: FAIL with `AttributeError: ... 'readback_section'`

- [ ] **Step 3: Implement the readback section**

Append to `src/openflight/iwr6843/firmware_checks.py` (add these imports at the top: `from openflight.iwr6843.dump import parse_header`, `from openflight.iwr6843.monitor import read_capture_config`, `from openflight.iwr6843.sparse import POWER_MAGIC, RANGE_FFT_SIZE, SPARSE_REQUEST_MAX_BYTES, SparsePlan, fit_cell_request, format_cell_request, parse_power, power_packet_size`, `from openflight.iwr6843.tracking import LOOP_PRI_S, RANGE_SPAN_M`):

```python
# Mirrors L3_SPARSE_REQUEST_TIMEOUT_MS in firmware/iwr6843/dump_format.h.
SPARSE_REQUEST_TIMEOUT_S = 5.0

TRACK_CFG_CASES: tuple[tuple[str, bool], ...] = (
    ("trackCfg 9e-05 0.046875 0 0 0", True),
    ("trackCfg 9e-05 0.046875 0 0", False),
    ("trackCfg -1 0.046875 0 0 0", False),
    ("trackCfg 0 0.046875 0 0 0", False),
    ("trackCfg 9e-05 0 0 0 0", False),
)


def _every_cell(summary) -> list[tuple[int, int]]:
    geometry = summary.geometry
    return [
        (frame, local)
        for frame in range(geometry.n_frames)
        for local in range(geometry.frame_bin_count(frame))
    ]


def _check_l3dump(ctx: Context) -> CheckResult:
    name = "readback/l3dump streams a valid dump"
    plan = stats_snapshot(ctx)
    raw = ctx.radar.read_dump()
    meta = parse_header(raw)
    after = stats_snapshot(ctx)
    problems = []
    want_frames = (plan.plan_pre or 0) + (plan.plan_post or 0)
    if meta["n_frames"] != want_frames:
        problems.append(f"n_frames={meta['n_frames']} want {want_frames}")
    loops = parse_stats(plan.raw).get("loops")
    if loops is not None and meta["chirps_per_frame"] != meta["n_tx"] * loops:
        problems.append(f"chirps_per_frame={meta['chirps_per_frame']} want n_tx*loops={meta['n_tx'] * loops}")
    if after.active != 1:
        problems.append(f"active={after.active} after l3dump")
    if problems:
        return failed(name, "; ".join(problems))
    return passed(name, f"{len(raw)} bytes, v{meta['version']}, {meta['n_frames']} frames")


def _check_sparse_limit(ctx: Context) -> CheckResult:
    name = "readback/l3sparse returns every cell at the limit"
    seen: dict[str, int] = {}

    def plan(summary):
        cells = _every_cell(summary)
        _request, sent = fit_cell_request(cells)
        seen["sent"] = sent
        # The first cell doubles as the noise cell; the rest fill the budget.
        return SparsePlan(cells=tuple(cells[1:sent]), noise_cells=(cells[0],))

    capture = ctx.radar.read_sparse(plan)
    if capture is None:
        return failed(name, "firmware refused l3sparse before freezing")
    detail = f"{capture.sent_cells}/{seen['sent']} cells, noise {capture.noise_power:.1f}"
    if capture.sent_cells != seen["sent"] or not capture.noise_power or capture.noise_power <= 0:
        return failed(name, detail)
    return passed(name, detail)


def _start_sparse(ctx: Context):
    ctx.radar.ser.reset_input_buffer()
    ctx.radar.ser.write(b"l3sparse\n")
    read = ctx.radar._read_packet(POWER_MAGIC, power_packet_size, 8.0)  # pylint: disable=protected-access
    if read is None:
        raise RuntimeError("firmware refused l3sparse")
    return parse_power(read[0])


def _cli_reply(ctx: Context, window_s: float) -> str:
    deadline = ctx.clock() + window_s
    reply = b""
    while ctx.clock() < deadline:
        waiting = ctx.radar.ser.in_waiting
        reply += ctx.radar.ser.read(waiting if waiting else 1)
        if b"Error" in reply and b"\n" in reply.split(b"Error", 1)[1]:
            break
        if not waiting:
            ctx.sleep(0.02)
    return reply.decode(errors="replace")


def _check_sparse_oversized(ctx: Context) -> CheckResult:
    name = "readback/l3sparse refuses an oversized request"
    summary = _start_sparse(ctx)
    request = format_cell_request(_every_cell(summary) * 4)
    if len(request) <= SPARSE_REQUEST_MAX_BYTES:
        return failed(name, f"test request only {len(request)} bytes; cannot exceed the limit")
    ctx.radar.ser.write(request)
    reply = _cli_reply(ctx, 3.0)
    health = ctx.radar.stats()
    problems = []
    if "longer than" not in reply:
        problems.append(f"not refused: {reply.strip()[-60:]!r}")
    if "Done" not in health or "not recognized" in health:
        problems.append(f"CLI dirty afterwards: {health.strip()[-60:]!r}")
    if problems:
        return failed(name, "; ".join(problems))
    return passed(name, f"{len(request)}-byte request refused, CLI clean")


def _check_sparse_late(ctx: Context) -> CheckResult:
    name = "readback/l3sparse refuses a late request, then works"
    _start_sparse(ctx)
    ctx.sleep(SPARSE_REQUEST_TIMEOUT_S + 0.5)
    reply = _cli_reply(ctx, 2.0)
    capture = ctx.radar.read_sparse(
        lambda summary: SparsePlan(cells=(), noise_cells=(_every_cell(summary)[0],))
    )
    problems = []
    if "request missing" not in reply:
        problems.append(f"late request not refused: {reply.strip()[-60:]!r}")
    if capture is None or not capture.noise_power or capture.noise_power <= 0:
        problems.append("next l3sparse did not work")
    if problems:
        return failed(name, "; ".join(problems))
    return passed(name, "late request refused, next exchange worked")


def _check_track_needs_cfg(ctx: Context) -> CheckResult:
    name = "readback/l3track without trackCfg is refused"
    before = stats_snapshot(ctx)
    reply = ctx.radar.cmd("l3track", 3.0)
    after = stats_snapshot(ctx)
    problems = []
    if "needs trackCfg" not in reply:
        problems.append(f"reply {reply.strip()[:60]!r}")
    if before.freeze_req != after.freeze_req:
        problems.append(f"freeze_req moved {before.freeze_req} -> {after.freeze_req}")
    if after.active != 1:
        problems.append(f"active={after.active}")
    if problems:
        return failed(name, "; ".join(problems))
    return passed(name, "refused without freezing")


def _check_track_cfg_validation(ctx: Context) -> CheckResult:
    return _run_validation_table(ctx, "readback/trackCfg validation", TRACK_CFG_CASES)


def track_config_command(cfg_path: str | Path) -> str:
    """``trackCfg`` with this cfg's loop period, the shared range resolution, and no clamps."""
    loop_period = read_capture_config(cfg_path).loop_period_s or LOOP_PRI_S
    fields = (loop_period, RANGE_SPAN_M / RANGE_FFT_SIZE, 0.0, 0.0, 0.0)
    return "trackCfg " + " ".join(f"{value:.17g}" for value in fields)


def _check_track_streams(ctx: Context) -> CheckResult:
    name = "readback/l3track streams the tracked cells"
    reply = ctx.radar.cmd(track_config_command(ctx.config), 2.0)
    if "Done" not in reply or "Error" in reply:
        return failed(name, f"trackCfg rejected: {reply.strip()[:60]!r}")
    before = stats_snapshot(ctx)
    started = ctx.clock()
    result = ctx.radar.read_tracked()
    elapsed = ctx.clock() - started
    if result is None:
        if before.format == "iq8":
            return skipped(name, "IQ8 profile: l3track not available")
        return failed(name, "firmware refused l3track before streaming")
    raw, noise, track = result
    after = stats_snapshot(ctx)
    problems = []
    if after.freeze_done is None or before.freeze_done is None or after.freeze_done != before.freeze_done + 1:
        problems.append(f"freeze_done {before.freeze_done} -> {after.freeze_done}")
    if after.active != 1:
        problems.append(f"active={after.active}")
    detail = f"{len(raw)} bytes, noise {noise:.1f}, found={track.found} inliers={track.n_inliers}, {elapsed:.2f}s"
    if problems:
        return failed(name, detail + "; " + "; ".join(problems))
    return passed(name, detail)


def readback_section() -> Section:
    """l3dump, l3sparse and l3track all stream and rearm on the default profile."""
    return Section(
        "readback",
        "active",
        (
            Check("readback/l3dump streams a valid dump", _check_l3dump),
            Check("readback/l3sparse returns every cell at the limit", _check_sparse_limit),
            Check("readback/l3sparse refuses an oversized request", _check_sparse_oversized),
            Check("readback/l3sparse refuses a late request, then works", _check_sparse_late),
            Check("readback/l3track without trackCfg is refused", _check_track_needs_cfg),
            Check("readback/trackCfg validation", _check_track_cfg_validation),
            Check("readback/l3track streams the tracked cells", _check_track_streams),
        ),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k "readback or l3dump or l3sparse or l3track or track_cfg" -v`
Expected: all passed. If `_check_l3dump` fails on `chirps_per_frame` with the fake cube, confirm the fake's `loops` in `_stats_for` equals `cube.shape[1] // n_tx` (it does: `chirps // n_tx`).

- [ ] **Step 5: Delete the superseded script**

```bash
git rm scripts/hardware-test/test_iwr_self_trigger.py
```

Search for references and remove any: `grep -rn "test_iwr_self_trigger" docs scripts README.md CLAUDE.md` should print nothing (it printed nothing during design).

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff check src/openflight/ tests/ && uv run ruff format --check src/openflight/ && uv run pylint src/openflight/iwr6843/firmware_checks.py --fail-under=9`

```bash
git add src/openflight/iwr6843/firmware_checks.py tests/test_iwr6843_firmware_checks.py
git commit -m "feat(iwr6843): add the readback firmware checks and retire test_iwr_self_trigger

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `trigger` section (hands-off)

**Files:**
- Modify: `src/openflight/iwr6843/firmware_checks.py`
- Modify: `tests/test_iwr6843_firmware_checks.py`

**Interfaces:**
- Consumes: `openflight.iwr6843.monitor.SelfTriggerConfig`, `SELF_TRIGGER_OFF_COMMAND`, `measure_trigger_level`, `tee_local_bin`; `openflight.iwr6843.self_trigger.FLOOR_PROBE_LEVEL`.
- Produces: `trigger_section() -> Section` named `"trigger"`, sensor `"active"`, eight checks named `trigger/fresh session untriggered`, `trigger/triggerCfg validation`, `trigger/arming starts the detector`, `trigger/triggerCfg 0 0 0 disarms`, `trigger/debugCfg streams parsable lines`, `trigger/debug lines only change on phase change`, `trigger/floor measurement`, `trigger/reconfigure clears a previous arm`.
- Produces: `TRIGGER_CFG_CASES`, `LIVE_PHASES = frozenset({"tee-low", "occupying", "watching", "no-approach", "toward", "away"})`, `TRIG_DEBUG_FIELDS = ("phase", "tee", "approach", "ready", "toward", "away", "run", "peak", "have", "bin", "level", "latched")`.
- Produces: `arm_command(ctx, level: float) -> str` — the `triggerCfg` line for `tee_local_bin(ctx.tee_m, ctx.config)`, `level`, `ctx.hits`.
- Check 1 sends `send_config` itself so it sees a fresh session regardless of what ran before.

- [ ] **Step 1: Write the failing trigger tests**

Append to `tests/test_iwr6843_firmware_checks.py`:

```python
def _trigger_radar(*, phases=("tee-low",), enabled_after_arm=1, latched=0, pre_seen=999, plan_pre=16, debug_lines=None):
    """A radar whose trig state follows the last triggerCfg it was sent."""
    state = {"enabled": 0, "phase": "off", "bin": 0, "level": 0, "n": 0}

    def stats(_n):
        phase = state["phase"] if state["enabled"] else "off"
        text = (
            f"frames={1000 + state['n'] * 10} active=1 rf_faults=0 freeze_req=0 freeze_done=0 "
            f"format=iq16 plan={plan_pre}pre/8post loops=12 used=1/2\npre_seen={pre_seen} stride=1\n"
            f"trig phase={phase} tee={412 if state['enabled'] else 0} latched={latched} enabled={state['enabled']}\nDone\n"
        )
        state["n"] += 1
        return text.encode()

    def trigger_cfg(line):
        fields = line.split()
        if len(fields) != 4 or not fields[1].isdigit() or not fields[3].isdigit() or fields[2].startswith("-"):
            return b"Error: triggerCfg <localBin> <power> <hits>\n"
        hits = int(fields[3])
        state["enabled"] = enabled_after_arm if hits else 0
        state["phase"] = phases[0]
        state["bin"], state["level"] = int(fields[1]), int(float(fields[2]))
        return b"Done\n"

    def debug_cfg(line):
        if line.split()[1] not in ("0", "1"):
            return b"Error: debugCfg <0|1>\n"
        if line.endswith("1"):
            lines = debug_lines or [
                f"trig phase={state['phase']} tee=412 approach=0 ready=1 toward=0 away=0 "
                f"run=0 peak=0 have=0 bin={state['bin']} level={state['level']} latched=0\n"
            ]
            return "".join(lines).encode() + b"Done\n"
        return b"Done\n"

    class Port(ScriptedSerial):
        def write(self, data):
            line = data.decode(errors="replace").strip()
            self.written.append(line)
            if line.startswith("triggerCfg"):
                self.inject(trigger_cfg(line))
            elif line.startswith("debugCfg"):
                self.inject(debug_cfg(line))
            elif line == "stats":
                self.inject(stats(0))
            else:
                self.inject(b"Done\n")

    from openflight.iwr6843.driver import IWR6843Radar

    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = Port({})
    radar.port = "scripted"
    radar._trigger_pending = b""
    radar.send_config = lambda cfg: state.update(enabled=0, phase="off")  # type: ignore[method-assign]
    return radar


def test_trigger_section_names_match_the_spec():
    assert _names(fc.trigger_section()) == [
        "trigger/fresh session untriggered",
        "trigger/triggerCfg validation",
        "trigger/arming starts the detector",
        "trigger/triggerCfg 0 0 0 disarms",
        "trigger/debugCfg streams parsable lines",
        "trigger/debug lines only change on phase change",
        "trigger/floor measurement",
        "trigger/reconfigure clears a previous arm",
    ]


def test_fresh_session_must_report_off_and_unlatched():
    check = fc.trigger_section().checks[0]

    assert check.run(_ctx(_trigger_radar())).status == "PASS"

    stale = _trigger_radar(latched=1)
    stale.send_config = lambda cfg: None  # a firmware that forgets to clear the latch
    result = check.run(_ctx(stale))
    assert result.status == "FAIL" and "latched=1" in result.detail


def test_trigger_cfg_validation_table():
    lines = dict(fc.TRIGGER_CFG_CASES)
    assert lines["triggerCfg 10 1000 2"] is True
    assert lines["triggerCfg 10 1000"] is False
    assert lines["triggerCfg x 1000 2"] is False
    assert lines["triggerCfg 10 -5 2"] is False
    assert lines["triggerCfg 10 1000 y"] is False

    assert fc.trigger_section().checks[1].run(_ctx(_trigger_radar())).status == "PASS"


def test_arming_needs_enabled_live_phase_full_ring_and_tee_power():
    check = fc.trigger_section().checks[2]

    good = check.run(_ctx(_trigger_radar()))
    assert good.status == "PASS", good.detail
    assert "tee=412" in good.detail

    not_enabled = check.run(_ctx(_trigger_radar(enabled_after_arm=0)))
    assert not_enabled.status == "FAIL" and "enabled=0" in not_enabled.detail

    short_ring = check.run(_ctx(_trigger_radar(pre_seen=3, plan_pre=16)))
    assert short_ring.status == "FAIL" and "pre_seen" in short_ring.detail

    stuck = check.run(_ctx(_trigger_radar(phases=("no-frame",))))
    assert stuck.status == "FAIL" and "no-frame" in stuck.detail


def test_disarm_returns_to_off():
    check = fc.trigger_section().checks[3]
    radar = _trigger_radar()

    assert check.run(_ctx(radar)).status == "PASS"
    assert radar.ser.written[0] == "triggerCfg 0 0 0"


def test_debug_cfg_lines_parse_and_echo_the_armed_values():
    check = fc.trigger_section().checks[4]

    good = check.run(_ctx(_trigger_radar()))
    assert good.status == "PASS", good.detail

    missing_field = _trigger_radar(debug_lines=["trig phase=tee-low tee=1 latched=0\n"])
    result = check.run(_ctx(missing_field))
    assert result.status == "FAIL" and "fields" in result.detail


def test_debug_stream_must_not_repeat_the_same_phase():
    check = fc.trigger_section().checks[5]

    quiet = _trigger_radar()
    assert check.run(_ctx(quiet)).status == "PASS"

    line = "trig phase=tee-low tee=1 approach=0 ready=1 toward=0 away=0 run=0 peak=0 have=0 bin=14 level=5 latched=0\n"
    chatty = _trigger_radar(debug_lines=[line] * 3)  # same phase written three times
    result = check.run(_ctx(chatty))
    assert result.status == "FAIL" and "repeated" in result.detail


def test_floor_measurement_uses_the_runtime_helper(monkeypatch):
    check = fc.trigger_section().checks[6]
    monkeypatch.setattr(fc, "measure_trigger_level", lambda radar, local_bin, hits, clock, pause: (300.0, 450.0))
    assert check.run(_ctx(_trigger_radar())).status == "PASS"

    def latched(*_a, **_k):
        raise RuntimeError("background sample latched the trigger")

    monkeypatch.setattr(fc, "measure_trigger_level", latched)
    result = check.run(_ctx(_trigger_radar()))
    assert result.status == "FAIL" and "latched" in result.detail


def test_reconfigure_must_clear_a_previous_arm():
    check = fc.trigger_section().checks[7]

    assert check.run(_ctx(_trigger_radar())).status == "PASS"

    sticky = _trigger_radar()
    sticky.send_config = lambda cfg: None
    result = check.run(_ctx(sticky))
    assert result.status == "FAIL" and "enabled=1" in result.detail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k "trigger or arming or disarm or debug or floor or reconfigure" -v`
Expected: FAIL with `AttributeError: ... 'trigger_section'`

- [ ] **Step 3: Implement the trigger section**

Append to `src/openflight/iwr6843/firmware_checks.py` (add imports: `from openflight.iwr6843.monitor import SELF_TRIGGER_OFF_COMMAND, SelfTriggerConfig, measure_trigger_level, read_capture_config, tee_local_bin` — merge with the existing monitor import — and `from openflight.iwr6843.self_trigger import FLOOR_PROBE_LEVEL`):

```python
TRIGGER_CFG_CASES: tuple[tuple[str, bool], ...] = (
    ("triggerCfg 10 1000 2", True),
    ("triggerCfg 10 1000", False),
    ("triggerCfg x 1000 2", False),
    ("triggerCfg 10 -5 2", False),
    ("triggerCfg 10 1000 y", False),
)
# Phases the detector reports while watching an empty or occupied tee (l3_triggerPhaseName).
LIVE_PHASES = frozenset({"tee-low", "occupying", "watching", "no-approach", "toward", "away"})
TRIG_DEBUG_FIELDS = (
    "phase", "tee", "approach", "ready", "toward", "away", "run", "peak", "have", "bin", "level", "latched",
)


def arm_command(ctx: Context, level: float) -> str:
    """``triggerCfg`` for this rig's tee bin at ``level``."""
    return SelfTriggerConfig(
        local_bin=tee_local_bin(ctx.tee_m, ctx.config), level=level, hits=ctx.hits
    ).command


def _trig_state(snap: StatsSnapshot) -> str:
    return f"phase={snap.phase} latched={snap.latched} enabled={snap.enabled}"


def _check_fresh_session(ctx: Context) -> CheckResult:
    name = "trigger/fresh session untriggered"
    ctx.radar.send_config(ctx.config)
    snap = stats_snapshot(ctx)
    if (snap.phase, snap.latched, snap.enabled) != ("off", 0, 0):
        return failed(name, _trig_state(snap))
    return passed(name, _trig_state(snap))


def _check_trigger_cfg_validation(ctx: Context) -> CheckResult:
    result = _run_validation_table(ctx, "trigger/triggerCfg validation", TRIGGER_CFG_CASES)
    ctx.radar.cmd(SELF_TRIGGER_OFF_COMMAND, 2.0)
    return result


def _check_arming(ctx: Context) -> CheckResult:
    name = "trigger/arming starts the detector"
    reply = ctx.radar.cmd(arm_command(ctx, FLOOR_PROBE_LEVEL), 2.0)
    if "Done" not in reply:
        return failed(name, f"arm rejected: {reply.strip()[:60]!r}")
    latest: dict[str, StatsSnapshot] = {}

    def live() -> bool:
        snap = stats_snapshot(ctx)
        latest["snap"] = snap
        return snap.enabled == 1 and snap.phase in LIVE_PHASES and (snap.tee or 0) > 0

    reached = wait_until(ctx, live, ctx.wait_s)
    snap = latest["snap"]
    ctx.radar.cmd(SELF_TRIGGER_OFF_COMMAND, 2.0)
    problems = []
    if snap.enabled != 1:
        problems.append(f"enabled={snap.enabled}")
    if snap.phase not in LIVE_PHASES:
        problems.append(f"phase={snap.phase} never went live")
    if snap.latched:
        problems.append("latched=1 on an empty lane")
    if snap.pre_seen is not None and snap.plan_pre is not None and snap.pre_seen < snap.plan_pre:
        problems.append(f"pre_seen={snap.pre_seen} < plan {snap.plan_pre}")
    if not reached or problems:
        return failed(name, "; ".join(problems) or _trig_state(snap))
    return passed(name, f"{_trig_state(snap)} tee={snap.tee} pre_seen={snap.pre_seen}")


def _check_disarm(ctx: Context) -> CheckResult:
    name = "trigger/triggerCfg 0 0 0 disarms"
    ctx.radar.cmd(SELF_TRIGGER_OFF_COMMAND, 2.0)
    snap = stats_snapshot(ctx)
    if (snap.enabled, snap.phase, snap.latched) != (0, "off", 0):
        return failed(name, _trig_state(snap))
    return passed(name, _trig_state(snap))


def _debug_lines(text: str) -> list[dict[str, str]]:
    """Every ``trig`` line in ``text`` (the debug stream carries no other trig lines)."""
    parsed = [parse_trig(line) for line in text.splitlines()]
    return [fields for fields in parsed if fields is not None]


def _check_debug_cfg(ctx: Context) -> CheckResult:
    name = "trigger/debugCfg streams parsable lines"
    local_bin = tee_local_bin(ctx.tee_m, ctx.config)
    ctx.radar.cmd(arm_command(ctx, FLOOR_PROBE_LEVEL), 2.0)
    try:
        reply = ctx.radar.cmd("debugCfg 1", 2.0)
        lines = _debug_lines(reply)
        problems = []
        if not lines:
            problems.append("no trig line in the debugCfg 1 reply")
        for fields in lines:
            missing = [key for key in TRIG_DEBUG_FIELDS if key not in fields]
            if missing:
                problems.append(f"missing fields {missing}")
                break
            if fields["bin"] != str(local_bin) or fields["level"] != str(int(FLOOR_PROBE_LEVEL)):
                problems.append(f"bin={fields['bin']} level={fields['level']} do not echo the armed values")
                break
        off = ctx.radar.cmd("debugCfg 0", 2.0)
        if "Done" not in off:
            problems.append("debugCfg 0 rejected")
        if _debug_lines(read_port_text(ctx, 0.5)):
            problems.append("trig lines still streaming after debugCfg 0")
        bad = ctx.radar.cmd("debugCfg 2", 2.0)
        if "Error" not in bad:
            problems.append("debugCfg 2 accepted")
    finally:
        ctx.radar.cmd("debugCfg 0", 2.0)
        ctx.radar.cmd(SELF_TRIGGER_OFF_COMMAND, 2.0)
    if problems:
        return failed(name, "; ".join(problems))
    return passed(name, f"{len(lines)} line(s), bin={local_bin} level={int(FLOOR_PROBE_LEVEL)}")


def _check_debug_change_only(ctx: Context) -> CheckResult:
    name = "trigger/debug lines only change on phase change"
    ctx.radar.cmd(arm_command(ctx, FLOOR_PROBE_LEVEL), 2.0)
    try:
        # The debugCfg 1 reply carries the first line; anything after Done streams on.
        lines = _debug_lines(ctx.radar.cmd("debugCfg 1", 2.0))
        lines += _debug_lines(read_port_text(ctx, 1.0))
    finally:
        ctx.radar.cmd("debugCfg 0", 2.0)
        ctx.radar.cmd(SELF_TRIGGER_OFF_COMMAND, 2.0)
    repeats = sum(1 for a, b in zip(lines, lines[1:]) if a["phase"] == b["phase"])
    if repeats:
        return failed(name, f"{repeats} repeated same-phase line(s) in 1 s")
    return passed(name, f"{len(lines)} line(s) in 1 s, no repeats")


def _check_floor(ctx: Context) -> CheckResult:
    name = "trigger/floor measurement"
    local_bin = tee_local_bin(ctx.tee_m, ctx.config)
    try:
        floor, level = measure_trigger_level(
            ctx.radar, local_bin, ctx.hits, clock=ctx.clock, pause=ctx.sleep
        )
    except RuntimeError as exc:
        ctx.radar.cmd(SELF_TRIGGER_OFF_COMMAND, 2.0)
        return failed(name, str(exc))
    ctx.radar.cmd(SELF_TRIGGER_OFF_COMMAND, 2.0)
    if not floor > 0 or not level > floor:
        return failed(name, f"floor={floor:.1f} level={level:.1f}")
    return passed(name, f"floor={floor:.1f} level={level:.1f}")


def _check_reconfigure_clears_arm(ctx: Context) -> CheckResult:
    name = "trigger/reconfigure clears a previous arm"
    ctx.radar.cmd(arm_command(ctx, FLOOR_PROBE_LEVEL), 2.0)
    ctx.radar.send_config(ctx.config)
    snap = stats_snapshot(ctx)
    if (snap.enabled, snap.latched, snap.phase) != (0, 0, "off"):
        return failed(name, _trig_state(snap))
    return passed(name, _trig_state(snap))


def trigger_section() -> Section:
    """Self-trigger lifecycle and observability without a swing."""
    return Section(
        "trigger",
        "active",
        (
            Check("trigger/fresh session untriggered", _check_fresh_session),
            Check("trigger/triggerCfg validation", _check_trigger_cfg_validation),
            Check("trigger/arming starts the detector", _check_arming),
            Check("trigger/triggerCfg 0 0 0 disarms", _check_disarm),
            Check("trigger/debugCfg streams parsable lines", _check_debug_cfg),
            Check("trigger/debug lines only change on phase change", _check_debug_change_only),
            Check("trigger/floor measurement", _check_floor),
            Check("trigger/reconfigure clears a previous arm", _check_reconfigure_clears_arm),
        ),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k "trigger or arming or disarm or debug or floor or reconfigure" -v`
Expected: all passed. The `_trigger_radar` fake's `stats` reply reads `tee_local_bin` from the real wide cfg (`20`-bin window start, 53 bins), so `bin=` echoes `tee_local_bin(1.575, WIDE_CFG)`; if the echo test fails, print both values and align the fake's `state["bin"]` with what `arm_command` sent (it records the sent bin, so they match by construction).

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src/openflight/ tests/ && uv run ruff format --check src/openflight/ && uv run pylint src/openflight/iwr6843/firmware_checks.py --fail-under=9`

```bash
git add src/openflight/iwr6843/firmware_checks.py tests/test_iwr6843_firmware_checks.py
git commit -m "feat(iwr6843): add the hands-off self-trigger firmware checks

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: `trigger-swing` section (prompted)

**Files:**
- Modify: `src/openflight/iwr6843/firmware_checks.py`
- Modify: `tests/test_iwr6843_firmware_checks.py`

**Interfaces:**
- Consumes: `IWR6843Radar.wait_trigger_notice`, `read_tracked`, `read_sparse`; `openflight.iwr6843.self_trigger.replay_dump`; `measure_trigger_level`; `arm_command`; `track_config_command`.
- Produces: `swing_section(shots: int) -> Section` named `"trigger-swing"`, sensor `"active"`, every check `needs_swing=True`. Check names per shot `N` (1-based): `trigger-swing/shot N: ball on tee reaches watching`, `trigger-swing/shot N: swing fires the trigger`, `trigger-swing/shot N: frozen ring reads back`, `trigger-swing/shot N: host replay agrees`, `trigger-swing/shot N: rearmed`; then `trigger-swing/latched session is cleared by reconfigure`.
- Produces: `wait_for_notice(ctx, timeout_s: float, poll_s: float = 0.5) -> float | None` — seconds waited until `Triggered` arrived, polling `stats` every `poll_s` through `ctx.radar.cmd` so the notice has to survive a command reply; `None` on timeout.
- Produces: `READBACK_LIMIT_S = 1.0`.
- Shared per-run state: a `_SwingState` dataclass (armed level, last raw dump) stored on the section closure so `host replay agrees` can reuse the dump `frozen ring reads back` fetched rather than freezing twice.
- Arming happens lazily inside shot 1's first check: `ctx.level` if given, else `measure_trigger_level`. A latch during measurement is a FAIL for that check and every later swing check is SKIP "not armed".

- [ ] **Step 1: Write the failing swing tests**

Append to `tests/test_iwr6843_firmware_checks.py`:

```python
def _swing_radar(cube, *, notice_in_stats_after=2, fire=True, watching=True, rearm=True, clear_on_reconfigure=True):
    """Scripted swing. Once armed, the tee goes ``watching`` on the second stats poll;
    ``notice_in_stats_after`` polls later a ``Triggered`` notice is planted inside a
    stats reply (exactly how the firmware's unsolicited line lands on the wire)."""
    state = {"stats": 0, "since_watching": None, "latched": 0, "freeze": 0, "enabled": 0, "phase": "tee-low", "armed": False}
    summary = _summary(cube)
    track = OnboardTrack(True, 3, 1.0, 2.0, 0.1, 0.0, 0.01)
    track_packet = summary.header_bytes(b"ILT1") + track.to_bytes() + slice_packet(cube, 3, [(0, 1)]) + b"Done\n"

    def stats(_n):
        state["stats"] += 1
        prefix = b""
        if state["armed"] and watching and state["phase"] == "tee-low" and state["stats"] >= 2:
            state["phase"], state["since_watching"] = "watching", 0
        elif state["since_watching"] is not None and state["phase"] == "watching":
            state["since_watching"] += 1
            if fire and state["since_watching"] == notice_in_stats_after:
                prefix = b"Triggered\n"
                state["latched"], state["freeze"], state["phase"] = 1, state["freeze"] + 1, "fired"
        body = (
            f"frames={state['stats'] * 100} active=1 freeze_req={state['freeze']} freeze_done={state['freeze']} "
            f"format=iq16 plan={cube.shape[0] - 1}pre/1post loops={cube.shape[1] // 3} used=1/2\n"
            f"pre_seen=999 stride=1\ntrig phase={state['phase']} tee=500 latched={state['latched']} enabled={state['enabled']}\nDone\n"
        ).encode()
        return prefix + body

    class Port(ScriptedSerial):
        def write(self, data):
            line = data.decode(errors="replace").strip()
            self.written.append(line)
            if line == "stats":
                self.inject(stats(0))
            elif line.startswith("triggerCfg"):
                state["armed"] = not line.endswith(" 0 0 0")
                state["enabled"] = 1 if state["armed"] else 0
                state["phase"], state["since_watching"], state["stats"] = "tee-low", None, 0
                self.inject(b"Done\n")
            elif line == "l3track":
                self.inject(track_packet)
                if rearm:  # the firmware rearms: unlatched, back to watching the tee
                    state["latched"], state["phase"], state["since_watching"], state["stats"] = 0, "tee-low", None, 0
            else:
                self.inject(b"Done\n")

    from openflight.iwr6843.driver import IWR6843Radar

    radar = IWR6843Radar.__new__(IWR6843Radar)
    radar.ser = Port({})
    radar.port = "scripted"
    radar._trigger_pending = b""

    def send_config(_cfg):
        if clear_on_reconfigure:
            state.update(latched=0, enabled=0, phase="off", armed=False, since_watching=None)

    radar.send_config = send_config
    return radar, state


def test_swing_fake_fires_two_polls_after_watching():
    """Pin the fake itself: tee-low, watching, then a notice inside the 2nd poll after that."""
    radar, state = _swing_radar(_cube())
    radar.ser.write(b"triggerCfg 14 1000 2\n")
    radar.ser.read(radar.ser.in_waiting)
    seen = []
    for _ in range(4):
        radar.ser.write(b"stats\n")
        seen.append(radar.ser.read(radar.ser.in_waiting))

    assert b"phase=tee-low" in seen[0]
    assert b"phase=watching" in seen[1]
    assert b"Triggered" not in seen[2]
    assert seen[3].startswith(b"Triggered\n") and b"latched=1" in seen[3]
    assert state["freeze"] == 1


def test_swing_section_names_and_flags():
    section = fc.swing_section(2)

    assert _names(section) == [
        "trigger-swing/shot 1: ball on tee reaches watching",
        "trigger-swing/shot 1: swing fires the trigger",
        "trigger-swing/shot 1: frozen ring reads back",
        "trigger-swing/shot 1: host replay agrees",
        "trigger-swing/shot 1: rearmed",
        "trigger-swing/shot 2: ball on tee reaches watching",
        "trigger-swing/shot 2: swing fires the trigger",
        "trigger-swing/shot 2: frozen ring reads back",
        "trigger-swing/shot 2: host replay agrees",
        "trigger-swing/shot 2: rearmed",
        "trigger-swing/latched session is cleared by reconfigure",
    ]
    assert all(check.needs_swing for check in section.checks)


def test_swing_notice_inside_a_stats_reply_is_seen():
    radar, _state = _swing_radar(_cube())
    radar.ser.write(b"triggerCfg 14 1000 2\n")
    radar.ser.read(radar.ser.in_waiting)

    waited = fc.wait_for_notice(_ctx(radar), timeout_s=5.0, poll_s=0.5)

    assert waited is not None
    assert radar.ser.written.count("stats") >= 2


def test_wait_for_notice_times_out_to_none():
    radar, _state = _swing_radar(_cube(), fire=False)

    assert fc.wait_for_notice(_ctx(radar, wait_s=1.0), timeout_s=1.0) is None


def test_full_swing_run_passes_with_a_scripted_operator(monkeypatch):
    cube = _cube()
    radar, _state = _swing_radar(cube)
    prompts: list[str] = []
    monkeypatch.setattr(fc, "replay_dump", lambda raw, **kw: [type("Obs", (), {"fired": True})()])
    ctx = _ctx(radar, prompt=prompts.append, shots=1)

    results = fc.run(ctx, (fc.swing_section(1),), swing=True)

    assert [r.status for r in results] == ["PASS"] * 6, [(r.name, r.detail) for r in results]
    assert any("ball" in p.lower() for p in prompts) and any("swing" in p.lower() for p in prompts)
    assert "trackCfg" in " ".join(radar.ser.written)


def test_swing_fire_check_fails_on_timeout_and_later_checks_skip(monkeypatch):
    radar, _state = _swing_radar(_cube(), fire=False)
    monkeypatch.setattr(fc, "replay_dump", lambda raw, **kw: [])
    ctx = _ctx(radar, wait_s=1.0, shots=1)

    results = fc.run(ctx, (fc.swing_section(1),), swing=True)

    statuses = {r.name.split("/", 1)[1]: r.status for r in results}
    assert statuses["shot 1: swing fires the trigger"] == "FAIL"
    assert statuses["shot 1: frozen ring reads back"] == "SKIP"
    assert statuses["shot 1: host replay agrees"] == "SKIP"


def test_readback_slower_than_the_limit_fails(monkeypatch):
    cube = _cube()
    radar, _state = _swing_radar(cube)
    slow = {"now": 0.0}

    def clock():
        slow["now"] += 0.6
        return slow["now"]

    monkeypatch.setattr(fc, "replay_dump", lambda raw, **kw: [type("Obs", (), {"fired": True})()])
    results = fc.run(_ctx(radar, clock=clock, shots=1), (fc.swing_section(1),), swing=True)

    readback = next(r for r in results if r.name.endswith("frozen ring reads back"))
    assert readback.status == "FAIL" and "1.0 s" in readback.detail


def test_host_replay_disagreement_fails(monkeypatch):
    radar, _state = _swing_radar(_cube())
    monkeypatch.setattr(fc, "replay_dump", lambda raw, **kw: [type("Obs", (), {"fired": False})()])

    results = fc.run(_ctx(radar, shots=1), (fc.swing_section(1),), swing=True)

    replay = next(r for r in results if r.name.endswith("host replay agrees"))
    assert replay.status == "FAIL"


def test_rearm_and_reconfigure_failures_are_reported(monkeypatch):
    monkeypatch.setattr(fc, "replay_dump", lambda raw, **kw: [type("Obs", (), {"fired": True})()])

    stuck, _ = _swing_radar(_cube(), rearm=False)
    results = fc.run(_ctx(stuck, shots=1), (fc.swing_section(1),), swing=True)
    assert next(r for r in results if r.name.endswith("rearmed")).status == "FAIL"

    sticky, _ = _swing_radar(_cube(), clear_on_reconfigure=False)
    results = fc.run(_ctx(sticky, shots=1), (fc.swing_section(1),), swing=True)
    assert results[-1].status == "FAIL" and "latched=1" in results[-1].detail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k "swing or notice or readback_slower or host_replay or rearm_and" -v`
Expected: FAIL with `AttributeError: ... 'swing_section'`

- [ ] **Step 3: Implement the swing section**

Append to `src/openflight/iwr6843/firmware_checks.py` (add `from openflight.iwr6843.self_trigger import FLOOR_PROBE_LEVEL, replay_dump`):

```python
READBACK_LIMIT_S = 1.0  # docs/iwr6843/verify.md: readback latency must stay under 1.0 s per shot


@dataclass
class _SwingState:
    level: float | None = None
    armed: bool = False
    last_dump: bytes | None = None
    fired: bool = False


def wait_for_notice(ctx: Context, timeout_s: float, poll_s: float = 0.5) -> float | None:
    """Seconds until ``Triggered`` arrives, polling ``stats`` so the notice must survive a reply."""
    started = ctx.clock()
    pending = b""
    last_poll = started
    while ctx.clock() - started < timeout_s:
        found, pending = ctx.radar.wait_trigger_notice(pending)
        if found:
            return ctx.clock() - started
        if ctx.clock() - last_poll >= poll_s:
            ctx.radar.cmd("stats", 2.0)  # cmd() keeps a notice it reads for the listener
            last_poll = ctx.clock()
        else:
            ctx.sleep(0.01)
    return None


def _arm_for_swing(ctx: Context, state: _SwingState) -> str | None:
    """Arm once; return an error string instead of arming when the lane is not empty."""
    if state.armed:
        return None
    if ctx.level is None:
        local_bin = tee_local_bin(ctx.tee_m, ctx.config)
        try:
            _floor, level = measure_trigger_level(
                ctx.radar, local_bin, ctx.hits, clock=ctx.clock, pause=ctx.sleep
            )
        except RuntimeError as exc:
            ctx.radar.cmd(SELF_TRIGGER_OFF_COMMAND, 2.0)
            return f"floor measurement failed: {exc}"
        state.level = level
    else:
        state.level = ctx.level
    reply = ctx.radar.cmd(arm_command(ctx, state.level), 2.0)
    if "Done" not in reply:
        return f"arm rejected: {reply.strip()[:60]!r}"
    ctx.radar.cmd(track_config_command(ctx.config), 2.0)
    state.armed = True
    return None


def _swing_checks(shot: int, state: _SwingState) -> tuple[Check, ...]:
    prefix = f"trigger-swing/shot {shot}"

    def ball_on_tee(ctx: Context) -> CheckResult:
        name = f"{prefix}: ball on tee reaches watching"
        problem = _arm_for_swing(ctx, state)
        if problem:
            return failed(name, problem)
        state.fired = False
        ctx.prompt(f"Shot {shot}: place a ball on the tee, then press Enter.")
        latest: dict[str, StatsSnapshot] = {}

        def watching() -> bool:
            latest["snap"] = stats_snapshot(ctx)
            return latest["snap"].phase == "watching"

        if not wait_until(ctx, watching, ctx.wait_s, poll_s=0.2):
            return failed(name, _trig_state(latest["snap"]))
        return passed(name, f"level={state.level:.0f} {_trig_state(latest['snap'])}")

    def swing_fires(ctx: Context) -> CheckResult:
        name = f"{prefix}: swing fires the trigger"
        if not state.armed:
            return skipped(name, "not armed")
        before = stats_snapshot(ctx)
        ctx.prompt(f"Shot {shot}: swing now. Waiting up to {ctx.wait_s:.0f} s.")
        waited = wait_for_notice(ctx, ctx.wait_s)
        if waited is None:
            return failed(name, f"no Triggered within {ctx.wait_s:.0f} s")
        notice_at = ctx.clock()
        latest: dict[str, StatsSnapshot] = {}

        def frozen() -> bool:
            latest["snap"] = stats_snapshot(ctx)
            snap = latest["snap"]
            return snap.latched == 1 and snap.freeze_done == snap.freeze_req

        settled = wait_until(ctx, frozen, ctx.wait_s, poll_s=0.05)
        snap = latest["snap"]
        problems = []
        if snap.latched != 1 or snap.phase != "fired":
            problems.append(_trig_state(snap))
        if before.freeze_req is not None and snap.freeze_req != before.freeze_req + 1:
            problems.append(f"freeze_req {before.freeze_req} -> {snap.freeze_req}")
        if not settled:
            problems.append(f"freeze_done={snap.freeze_done} != freeze_req={snap.freeze_req}")
        if problems:
            return failed(name, "; ".join(problems))
        state.fired = True
        return passed(name, f"notice after {waited:.1f} s, latched {ctx.clock() - notice_at:.3f} s later")

    def reads_back(ctx: Context) -> CheckResult:
        name = f"{prefix}: frozen ring reads back"
        if not state.fired:
            return skipped(name, "no fire to read back")
        plan = stats_snapshot(ctx)
        started = ctx.clock()
        tracked = ctx.radar.read_tracked()
        if tracked is not None:
            raw = tracked[0]
        else:
            capture = ctx.radar.read_sparse(
                lambda summary: SparsePlan(cells=tuple(_every_cell(summary)[1:]), noise_cells=(_every_cell(summary)[0],))
            )
            if capture is None:
                return failed(name, "both l3track and l3sparse refused")
            raw = capture.raw
        elapsed = ctx.clock() - started
        state.last_dump = raw
        meta = parse_header(raw)
        want_frames = (plan.plan_pre or 0) + (plan.plan_post or 0)
        problems = []
        if elapsed >= READBACK_LIMIT_S:
            problems.append(f"readback {elapsed:.2f} s >= {READBACK_LIMIT_S:.1f} s")
        if meta["n_frames"] != want_frames:
            problems.append(f"n_frames={meta['n_frames']} want {want_frames}")
        if plan.pre_seen is not None and plan.plan_pre is not None and plan.pre_seen < plan.plan_pre:
            problems.append(f"pre_seen={plan.pre_seen} < plan {plan.plan_pre}")
        if problems:
            return failed(name, "; ".join(problems))
        return passed(name, f"{len(raw)} bytes via {'l3track' if tracked else 'l3sparse'} in {elapsed:.2f} s")

    def replay_agrees(ctx: Context) -> CheckResult:
        name = f"{prefix}: host replay agrees"
        if state.last_dump is None:
            return skipped(name, "no ring to replay")
        observations = replay_dump(
            state.last_dump, tee_range_m=ctx.tee_m, level=state.level or FLOOR_PROBE_LEVEL, hits=ctx.hits
        )
        fired_at = next((i for i, obs in enumerate(observations) if obs.fired), None)
        state.last_dump = None
        if fired_at is None:
            return failed(name, f"host detector did not fire over {len(observations)} frames")
        return passed(name, f"host detector fired at frame {fired_at}")

    def rearmed(ctx: Context) -> CheckResult:
        name = f"{prefix}: rearmed"
        if not state.fired:
            return skipped(name, "no fire to rearm from")
        first = stats_snapshot(ctx)
        ctx.sleep(0.2)
        second = stats_snapshot(ctx)
        problems = []
        if second.latched != 0:
            problems.append(f"latched={second.latched}")
        if second.enabled != 1:
            problems.append(f"enabled={second.enabled}")
        if first.frames is None or second.frames is None or second.frames <= first.frames:
            problems.append(f"frames {first.frames} -> {second.frames}")
        if problems:
            return failed(name, "; ".join(problems))
        return passed(name, _trig_state(second))

    return (
        Check(f"{prefix}: ball on tee reaches watching", ball_on_tee, needs_swing=True),
        Check(f"{prefix}: swing fires the trigger", swing_fires, needs_swing=True),
        Check(f"{prefix}: frozen ring reads back", reads_back, needs_swing=True),
        Check(f"{prefix}: host replay agrees", replay_agrees, needs_swing=True),
        Check(f"{prefix}: rearmed", rearmed, needs_swing=True),
    )


def _latched_cleared(state: _SwingState) -> Check:
    name = "trigger-swing/latched session is cleared by reconfigure"

    def run(ctx: Context) -> CheckResult:
        if not state.armed:
            return skipped(name, "not armed")
        ctx.prompt("One more swing, which will NOT be read back. Place the ball, swing, then wait.")
        if wait_for_notice(ctx, ctx.wait_s) is None:
            return failed(name, f"no Triggered within {ctx.wait_s:.0f} s")
        ctx.radar.send_config(ctx.config)
        snap = stats_snapshot(ctx)
        if (snap.latched, snap.enabled, snap.phase) != (0, 0, "off"):
            return failed(name, _trig_state(snap))
        return passed(name, _trig_state(snap))

    return Check(name, run, needs_swing=True)


def swing_section(shots: int) -> Section:
    """Real swings: fire, read back, host agreement, rearm; then a latched reconfigure."""
    state = _SwingState()
    checks: list[Check] = []
    for shot in range(1, shots + 1):
        checks.extend(_swing_checks(shot, state))
    checks.append(_latched_cleared(state))
    return Section("trigger-swing", "active", tuple(checks))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k "swing or notice or readback_slower or host_replay or rearm_and" -v`
Expected: all passed. `test_swing_fake_fires_two_polls_after_watching` pins the fake's own sequence (tee-low, watching, quiet, notice), so if `test_full_swing_run_passes_with_a_scripted_operator` fails, read the failing check's detail from the assertion message: it names which field the library saw. Fix the library only if the fake's pinned sequence is what real firmware does (it is: `ball on tee` waits for `watching`, then `swing fires` polls `stats` until the notice lands).

- [ ] **Step 5: Lint and commit**

Run: `uv run ruff check src/openflight/ tests/ && uv run ruff format --check src/openflight/ && uv run pylint src/openflight/iwr6843/firmware_checks.py --fail-under=9`

```bash
git add src/openflight/iwr6843/firmware_checks.py tests/test_iwr6843_firmware_checks.py
git commit -m "feat(iwr6843): add the prompted swing firmware checks

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: `solve` placeholder, `SECTIONS`, CLI-table pinning, and the script

**Files:**
- Modify: `src/openflight/iwr6843/firmware_checks.py`
- Create: `scripts/hardware-test/test_iwr_firmware.py`
- Create: `tests/test_iwr6843_firmware_cli.py`
- Modify: `tests/test_iwr6843_firmware_checks.py`

**Interfaces:**
- Produces: `solve_section() -> Section` named `"solve"`, sensor `"any"`, one check `solve/on-chip solve` returning SKIP `"no CLI entry point in this firmware image"`.
- Produces: `build_sections(profiles: tuple[str, ...], shots: int) -> tuple[Section, ...]` in order lifecycle, profiles, readback, trigger, trigger-swing, solve.
- Produces: `COMMANDS_COVERED: frozenset[str]` — every firmware CLI command some check sends: `{"sensorStart", "sensorStop", "l3dump", "stats", "captureCfg", "phaseCaptureCfg", "captureFormat", "iq8Scale", "l3sparse", "triggerCfg", "l3track", "trackCfg", "debugCfg"}` (`sensorStart` is sent through `send_config`).
- Produces: `default_profiles() -> tuple[str, ...]` — sorted `config/iwr6843_*.cfg` paths relative to the repo root.
- Script: `main(argv: list[str] | None = None) -> int` with `build_parser()`; `--list` prints sections and check names and returns 0 without opening a port; unknown `--only` names return 2 with a message before any port is opened.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_iwr6843_firmware_checks.py`:

```python
import re
from pathlib import Path

FIRMWARE = Path(__file__).resolve().parents[1] / "firmware" / "iwr6843" / "l3_dump.c"


def test_solve_section_is_an_explicit_skip():
    section = fc.solve_section()

    result = section.checks[0].run(_ctx(scripted_radar({})))

    assert section.name == "solve" and section.sensor == "any"
    assert result == fc.CheckResult("solve/on-chip solve", "SKIP", "no CLI entry point in this firmware image")


def test_build_sections_orders_the_catalogue():
    sections = fc.build_sections(("a.cfg",), shots=1)

    assert [s.name for s in sections] == ["lifecycle", "profiles", "readback", "trigger", "trigger-swing", "solve"]


def test_every_registered_firmware_cli_command_has_a_check():
    """A new tableEntry[n].cmd in l3_dump.c without a check must fail CI (HWA smoke commands excluded)."""
    source = FIRMWARE.read_text(encoding="utf-8")
    table = source[source.index("cliCfg.tableEntry[0].cmd") : source.index("CLI_open(&cliCfg)")]
    smoke_free = re.sub(r"#ifdef ENABLE_HWA_SMOKE.*?#endif", "", table, flags=re.S)
    registered = set(re.findall(r'\.cmd\s*=\s*"(\w+)"', smoke_free))

    assert registered == fc.COMMANDS_COVERED


def test_default_profiles_are_the_shipped_cfgs():
    profiles = fc.default_profiles()

    assert profiles and all(Path(p).name.startswith("iwr6843_") and p.endswith(".cfg") for p in profiles)
    assert profiles == tuple(sorted(profiles))
```

Create `tests/test_iwr6843_firmware_cli.py`:

```python
"""Argument handling of scripts/hardware-test/test_iwr_firmware.py (no hardware)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hardware-test" / "test_iwr_firmware.py"


def _load():
    spec = importlib.util.spec_from_file_location("test_iwr_firmware_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_list_prints_every_section_and_check_without_a_port(capsys, monkeypatch):
    script = _load()
    monkeypatch.setattr(script, "IWR6843Radar", None)  # opening a port would raise TypeError

    code = script.main(["--list"])

    out = capsys.readouterr().out
    assert code == 0
    assert "lifecycle (sensor: active)" in out
    assert "trigger-swing (sensor: active, needs --swing)" in out
    assert "  trigger/fresh session untriggered" in out


def test_cli_rejects_unknown_only_before_opening_a_port(capsys, monkeypatch):
    script = _load()
    monkeypatch.setattr(script, "IWR6843Radar", None)

    code = script.main(["--only", "lifecycle,zzz"])

    assert code == 2
    assert "unknown section: zzz" in capsys.readouterr().err


def test_swing_with_only_that_excludes_the_swing_section_is_an_error(capsys, monkeypatch):
    script = _load()
    monkeypatch.setattr(script, "IWR6843Radar", None)

    code = script.main(["--swing", "--only", "lifecycle"])

    assert code == 2
    assert "trigger-swing" in capsys.readouterr().err


def test_windows_port_name_is_refused_on_the_pi(capsys, monkeypatch):
    script = _load()
    monkeypatch.setattr(script, "IWR6843Radar", None)
    monkeypatch.setattr(script.sys, "platform", "linux")

    code = script.main(["--port", "COM5"])

    assert code == 2
    assert "leave --port off" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py -k "solve or build_sections or registered or default_profiles" tests/test_iwr6843_firmware_cli.py -v`
Expected: FAIL with `AttributeError: ... 'solve_section'` and `FileNotFoundError` for the script

- [ ] **Step 3: Add the solve section, catalogue, and coverage set**

Append to `src/openflight/iwr6843/firmware_checks.py`:

```python
def _check_solve(_ctx: Context) -> CheckResult:
    return skipped("solve/on-chip solve", "no CLI entry point in this firmware image")


def solve_section() -> Section:
    """The DSS solve ships in the image but the MSS exposes no command for it yet."""
    return Section("solve", "any", (Check("solve/on-chip solve", _check_solve),))


# Every firmware CLI command some check sends; test_every_registered_firmware_cli_command_has_a_check
# compares this with the CLI table in firmware/iwr6843/l3_dump.c.
COMMANDS_COVERED = frozenset(
    {
        "sensorStart",  # through IWR6843Radar.send_config
        "sensorStop",
        "l3dump",
        "stats",
        "captureCfg",
        "phaseCaptureCfg",
        "captureFormat",
        "iq8Scale",
        "l3sparse",
        "triggerCfg",
        "l3track",
        "trackCfg",
        "debugCfg",
    }
)


def default_profiles() -> tuple[str, ...]:
    """Every shipped IWR6843 capture profile as a repo-relative path, sorted."""
    root = Path(__file__).resolve().parents[3]  # src/openflight/iwr6843 -> repo root
    return tuple(f"config/{path.name}" for path in sorted((root / "config").glob("iwr6843_*.cfg")))


def build_sections(profiles: tuple[str, ...], shots: int) -> tuple[Section, ...]:
    """The full catalogue in run order."""
    return (
        lifecycle_section(),
        profiles_section(profiles),
        readback_section(),
        trigger_section(),
        swing_section(shots),
        solve_section(),
    )
```

- [ ] **Step 4: Write the script**

Create `scripts/hardware-test/test_iwr_firmware.py`:

```python
#!/usr/bin/env python3
"""Exercise every CLI command of the IWR6843 L3-dump firmware on a connected board.

Stop the kiosk first; this owns the TI UART. Each check prints PASS, FAIL or
SKIP and the script exits 1 if any check fails. SKIP never fails the run.

Sections (see --list): lifecycle, profiles, readback, trigger, trigger-swing,
solve. The trigger-swing section needs --swing and prompts you to place a
ball and swing; the others run hands-off.

    uv run python scripts/hardware-test/test_iwr_firmware.py
    uv run python scripts/hardware-test/test_iwr_firmware.py --only trigger
    uv run python scripts/hardware-test/test_iwr_firmware.py --swing --tee-m 1.575 --shots 2
    uv run python scripts/hardware-test/test_iwr_firmware.py --json /tmp/fw.json
"""

from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "src")

from openflight.iwr6843 import firmware_checks as fc  # noqa: E402
from openflight.iwr6843.calibration import DEFAULT_TEE_RANGE_M  # noqa: E402
from openflight.iwr6843.driver import IWR6843Radar  # noqa: E402

DEFAULT_CONFIG = "config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"


def port_name_error(port: str | None, platform: str) -> str | None:
    """Windows ``COMn`` names are not device paths on the Pi."""
    if not port or platform == "win32":
        return None
    suffix = port[3:]
    if port.upper().startswith("COM") and suffix.isdigit():
        return (
            f"{port} is a Windows port name. On this machine leave --port off, "
            "or pass a device path such as /dev/ttyUSB0."
        )
    return None


def build_parser() -> argparse.ArgumentParser:
    """Command-line flags."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 2)[1])
    parser.add_argument("--port", default=None, help="serial port (default: auto-detect)")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="profile for the default sections")
    parser.add_argument("--only", default=None, help="comma-separated section names to run")
    parser.add_argument("--list", action="store_true", help="print the catalogue and exit")
    parser.add_argument("--swing", action="store_true", help="run the prompted swing checks")
    parser.add_argument("--shots", type=int, default=2, help="swings to validate with --swing")
    parser.add_argument("--tee-m", type=float, default=DEFAULT_TEE_RANGE_M)
    parser.add_argument("--level", type=float, default=None, help="trigger level; default measures the floor")
    parser.add_argument("--hits", type=int, default=2)
    parser.add_argument("--wait-s", type=float, default=60.0, help="deadline for prompted and polled steps")
    parser.add_argument("--json", default=None, help="write results to this JSON file")
    parser.add_argument("--fail-fast", action="store_true", help="stop at the first FAIL")
    return parser


def print_catalogue(sections: tuple[fc.Section, ...]) -> None:
    """``--list`` output: one line per section, indented check names."""
    for section in sections:
        needs = ", needs --swing" if any(c.needs_swing for c in section.checks) else ""
        print(f"{section.name} (sensor: {section.sensor}{needs})")
        for check in section.checks:
            print(f"  {check.name}")


def _prompt(text: str) -> None:
    input(f"\n>>> {text} ")


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = build_parser().parse_args(argv)
    sections = fc.build_sections(fc.default_profiles(), args.shots)
    if args.list:
        print_catalogue(sections)
        return 0
    only = tuple(name.strip() for name in args.only.split(",")) if args.only else None
    try:
        selected = fc.select_sections(sections, only)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.swing and not any(s.name == "trigger-swing" for s in selected):
        print("error: --swing needs the trigger-swing section in --only", file=sys.stderr)
        return 2
    problem = port_name_error(args.port, sys.platform)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2

    radar = IWR6843Radar(args.port)
    ctx = fc.Context(
        radar=radar,
        config=args.config,
        tee_m=args.tee_m,
        level=args.level,
        hits=args.hits,
        wait_s=args.wait_s,
        shots=args.shots,
        profiles=fc.default_profiles(),
        prompt=_prompt,
        sleep=time.sleep,
        clock=time.monotonic,
        out=print,
    )
    print(f"IWR6843 on {radar.port}, default profile {args.config}")
    results: list[fc.CheckResult] = []
    interrupted = False
    try:
        results = fc.run(ctx, sections, only=only, swing=args.swing, fail_fast=args.fail_fast)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        interrupted = True
    results.extend(fc.cleanup(ctx))
    radar.close()
    if args.json:
        fc.write_json(results, args.json)
    if interrupted:
        return 130
    failed = sum(1 for r in results if r.status == fc.FAIL)
    print("ALL PASS" if failed == 0 else f"{failed} FAILURE(S) ABOVE")
    return fc.exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
```

Note that a Ctrl+C during `fc.run` leaves `results` as the empty list because the assignment never completed; the checks already printed to the terminal are the operator's record, and cleanup still runs. That matches the spec ("prints the summary so far and exits 130").

Note on `--list` with the port monkeypatched to `None`: `main` must not touch `IWR6843Radar` before the `--list`, `--only`, `--swing`, and port-name exits, which the order above guarantees.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_iwr6843_firmware_checks.py tests/test_iwr6843_firmware_cli.py -v`
Expected: all passed

Run: `uv run python scripts/hardware-test/test_iwr_firmware.py --list`
Expected: the six sections with their check names, no port opened.

- [ ] **Step 6: Full suite, lint, commit**

Run: `uv run pytest tests/ -q`
Expected: all passed

Run: `uv run ruff check src/openflight/ scripts/ tests/ && uv run ruff format --check src/openflight/ && uv run pylint src/openflight/ --fail-under=9`

```bash
git add src/openflight/iwr6843/firmware_checks.py scripts/hardware-test/test_iwr_firmware.py tests/test_iwr6843_firmware_checks.py tests/test_iwr6843_firmware_cli.py
git commit -m "feat(iwr6843): add the firmware CLI test suite script

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Documentation

**Files:**
- Modify: `docs/iwr6843/verify.md` (insert a section before `## Cadence Acceptance Soak`)
- Modify: `docs/development/firmware.md` (add a row to the Current Release table and a short paragraph after "Verify the checked-in image before flashing")

- [ ] **Step 1: Add the firmware feature check section to the verify guide**

Insert into `docs/iwr6843/verify.md` immediately before `## Cadence Acceptance Soak`:

```markdown
## Firmware Feature Check

After flashing a firmware image, or after any firmware change, run the CLI
test suite. Stop the kiosk first; the suite owns the TI UART.

```bash
uv run python scripts/hardware-test/test_iwr_firmware.py
```

It exercises every command the firmware registers on its CLI and prints one
`PASS`, `FAIL`, or `SKIP` line per check, grouped into sections:

| Section | What it proves | Hands-off? |
|---|---|---|
| `lifecycle` | `sensorStart`/`sensorStop`/`stats` behave; config commands are refused while active; a restart resets counters | yes |
| `profiles` | `captureCfg`, `phaseCaptureCfg`, `captureFormat`, `iq8Scale` validate their arguments; every shipped `config/iwr6843_*.cfg` loads with the declared format and stride | yes |
| `readback` | `l3dump`, `l3sparse` (limit, oversized, late request), `trackCfg`, and `l3track` stream and rearm | yes |
| `trigger` | a fresh session is untriggered, `triggerCfg` arms and disarms, the detector goes live only once the pre-trigger ring is full, `debugCfg` streams parsable change-only lines, the floor measurement works, and reconfiguring clears a previous arm | yes |
| `trigger-swing` | with `--swing`: a ball on the tee reaches `watching`, a swing fires `Triggered` (the notice must survive a `stats` reply), the frozen ring reads back in under 1.0 s, the host detector replay agrees, the ring rearms, and a latched session is cleared by reconfigure | no, prompts you |
| `solve` | always `SKIP`: the on-chip DSS solve is in the image but the MSS exposes no CLI entry point for it yet | yes |

Run only one section, or add the prompted swing checks:

```bash
uv run python scripts/hardware-test/test_iwr_firmware.py --only trigger
uv run python scripts/hardware-test/test_iwr_firmware.py --swing --tee-m 1.575 --shots 2
```

`--list` prints every check without opening a port. `--json path` writes the
results for a report. The suite exits 1 on any `FAIL`; `SKIP` lines (older
firmware, `--swing` not given, the solve placeholder) never fail the run.

The check logic is unit-tested without hardware in
`tests/test_iwr6843_firmware_checks.py` against a scripted serial port, and a
test pins the suite's command list to the CLI table in
`firmware/iwr6843/l3_dump.c`, so a new firmware command without a check fails
CI.
```

- [ ] **Step 2: Point the firmware developer guide at the suite**

In `docs/development/firmware.md`, add to the Current Release table after the `Flash SHA-256` row:

```markdown
| Validate on hardware | `uv run python scripts/hardware-test/test_iwr_firmware.py` (see [Firmware Feature Check](../iwr6843/verify.md#firmware-feature-check)) |
```

And after the `sha256sum` code block, add:

```markdown
After flashing, run the firmware CLI test suite on the Pi. It covers every
registered CLI command; the on-chip solve reports `SKIP` until the MSS gains a
command that invokes it.
```

- [ ] **Step 3: Check the docs build if the repo builds them**

Run: `uv run zensical build 2>/dev/null || true` (only if `zensical` is configured; otherwise skip). Then `grep -n "test_iwr_self_trigger" -r docs` must print nothing.

- [ ] **Step 4: Commit**

```bash
git add docs/iwr6843/verify.md docs/development/firmware.md
git commit -m "docs(iwr6843): document the firmware CLI test suite

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Self-review notes

- **Spec coverage:** runner (Task 3), parsers/snapshot (Task 1), fakes (Task 2), lifecycle (4), profiles (5), readback plus deletion of the old script (6), trigger (7), trigger-swing (8), solve/catalogue/pinning/script (9), docs (10). The spec's `StatsSnapshot` field list, section sensor states, SKIP semantics, cleanup behaviour, JSON shape, Ctrl+C exit 130, and port-name guard all map to a task.
- **Type consistency:** `Context` fields are the same in every task's `_ctx` helper; `Section.sensor` strings are `"active"|"stopped"|"any"` everywhere; check names in the section tests match the spec catalogue verbatim; `wait_for_notice`, `arm_command`, `track_config_command`, `expected_profile_shape`, `select_sections`, `build_sections`, `default_profiles` are defined before they are used.
- **Review Focus:** items 1-5 are pinned by the named tests in Tasks 8, 3, 2, 9.
