# IWR6843 Firmware CLI Test Suite

Date: 2026-09-26
Status: approved design, awaiting implementation plan

## Goal

One command, run on the Pi with the IWR6843 attached, that exercises every
command the shipped firmware image registers on its CLI and reports PASS,
FAIL, or SKIP per check with a non-zero exit on any FAIL. Triggering is the
deepest section. Hands-off checks run by default. Checks that need a
physical action (a ball on the tee, a swing) are opt-in and prompt the
operator.

Every parser and decision in the suite is unit-tested without hardware, in
the same style as the existing driver tests.

## Non-goals

- No hand-wave proxy for a swing. The ball-leave rule needs a real departure.
- No judgement of timing numbers the repo has not already committed to. The
  only enforced latency bound is the existing "readback under 1.0 s per shot"
  from `docs/iwr6843/verify.md`. Rearm latency and notice latency are
  reported.
- The on-chip DSS solve is in the shipped image but the MSS exposes no CLI
  command or mailbox dispatch for it. The suite reports that as a SKIP with
  the reason, so its absence is visible rather than silently untested.
- The cadence soak stays a separate script. It runs for minutes and is an
  acceptance gate for memory-layout changes, not a feature check.

## Files

| Path | Change |
|---|---|
| `src/openflight/iwr6843/firmware_checks.py` | New. Parsers, context, check functions, section catalogue, runner. |
| `scripts/hardware-test/test_iwr_firmware.py` | New. Argument parsing and a call into the runner. |
| `scripts/hardware-test/test_iwr_self_trigger.py` | Deleted. Its four checks move into the suite. |
| `scripts/hardware-test/iwr6843_cadence_soak.py` | Imports `parse_stats` from `firmware_checks` instead of defining it. |
| `scripts/iwr6843/swing_trigger.py` | Imports `parse_trig` from `firmware_checks` instead of defining it. |
| `tests/iwr6843_fakes.py` | Adds `ScriptedSerial`. |
| `tests/test_iwr6843_firmware_checks.py` | New. Unit tests for everything in `firmware_checks.py`. |
| `tests/test_iwr6843_monitor.py` | Cadence-soak parser tests point at the shared `parse_stats`. |
| `docs/iwr6843/verify.md` | Documents the suite as the firmware feature check. |
| `docs/development/firmware.md` | Points at the suite from the release table. |

## Runner

### Data model

```python
@dataclass(frozen=True)
class CheckResult:
    name: str            # "trigger/fresh session untriggered"
    status: Literal["PASS", "FAIL", "SKIP"]
    detail: str = ""     # one line, shown after the name
    seconds: float = 0.0

@dataclass(frozen=True)
class Check:
    name: str
    run: Callable[[Context], CheckResult]
    needs_swing: bool = False

@dataclass(frozen=True)
class Section:
    name: str            # "lifecycle", "profiles", "readback", "trigger", "trigger-swing", "solve"
    sensor: Literal["active", "stopped", "any"]
    checks: tuple[Check, ...]
```

`Context` carries the open `IWR6843Radar`, the default config path, tee
range, level (or None to measure), hits, `--wait-s`, a `prompt(text)`
callable, a `sleep(seconds)` callable, a `clock()` callable, and an output
sink. Tests inject all four callables so no unit test waits on a clock.

### Selection and output

- `run(ctx, sections, *, only=None, swing=False, fail_fast=False) -> list[CheckResult]`.
- `--only a,b` keeps catalogue order. Unknown section names are an argument
  error before the port is opened.
- Checks with `needs_swing=True` run only with `--swing`; otherwise they are
  reported as SKIP with "needs --swing" so the operator sees what was not
  covered.
- One line per check: `  PASS  trigger/fresh session untriggered: phase=off latched=0 enabled=0`.
  A per-section line at the end: `trigger: 8 pass, 0 fail, 0 skip`.
- Exit 0 when no check is FAIL. SKIP never fails the run.
- `--json path` writes `[{"name", "status", "detail", "seconds"}, ...]`.
- `--list` prints the catalogue with the sensor state each section needs and
  exits without opening a port.

### State reconciliation

Before each section the runner reads `stats` and brings the sensor to the
section's declared state: `send_config` for `active`, `stop_sensor` for
`stopped`. A failed check therefore does not cascade into the next section.
Reconciliation failure marks every check in that section FAIL with the
reconciliation error and moves on.

### Guards and cleanup

- Every check runs inside a guard. An exception becomes FAIL with the
  exception text and the run continues (unless `--fail-fast`).
- `UnsupportedCommand` becomes SKIP with "older firmware: <command>".
- Cleanup always runs in `finally`: `triggerCfg 0 0 0`, `debugCfg 0`,
  `stop_sensor`, `close`. Each cleanup failure is its own reported line and
  forces exit 1.
- Prompted steps take a deadline from `--wait-s` and become FAIL on timeout.
- Ctrl+C prints the summary so far and exits 130.
- The swing section refuses to arm when the floor measurement latches the
  trigger (lane not empty). The port-name guard from `swing_trigger.py`
  (`COMn` on a non-Windows host) is reused.

## Shared parsers

`parse_stats(text) -> dict[str, int]` is the cadence soak's regex parser,
moved. It is extended to also return the trig line's `phase` under a
separate helper, `parse_trig(line) -> dict[str, str] | None`, moved from
`swing_trigger.py`. A `stats_snapshot(ctx) -> StatsSnapshot` wraps both and
exposes `active`, `frames`, `pre_seen`, `plan_pre`, `freeze_req`,
`freeze_done`, `phase`, `tee`, `latched`, `enabled`, `format`, `stride`,
`used`, `capacity`, and the rearm fields as typed attributes. Missing fields
are `None` so checks can SKIP on older firmware instead of raising.

## Check catalogue

Names below are the exact strings the runner prints.

### lifecycle (sensor: active, default profile)

1. `config accepted`: `send_config` succeeds; `active=1`, `rf_faults=0`.
2. `frames advance`: `frames` grows across 0.5 s.
3. `config commands refused while active`: `captureCfg`, `phaseCaptureCfg`,
   `captureFormat iq16`, and `iq8Scale 64` each reply with an `Error`
   containing "stop the sensor".
4. `sensorStop idles the sensor`: `stop_sensor` succeeds; `stats` still
   answers with `active=0`.
5. `restart resets counters`: a second `send_config` gives `active=1` and a
   `frames` value below the count observed in check 2.

### profiles (sensor: stopped)

1. `captureCfg validation`: wrong argument count, a non-integer, a value
   above 255, zero pre bins, a window past 128 bins, and post frames at
   `L3_MAX_CAPTURE_FRAMES` are each refused with `Error`; the shipped
   6-value and 7-value forms are accepted.
2. `phaseCaptureCfg validation`: same shape of table for the 11-value form,
   including total frames at the cap.
3. `captureFormat`: `iq16` and `iq8` echo `Capture format: <fmt>`; `iq32`
   and a missing argument are refused.
4. `iq8Scale`: 16, 32, 64, 128, 256 echo `IQ8 fixed scale: <n>`; 8, 512,
   48, and `abc` are refused.
5. `profile <name> loads`: one check per `config/iwr6843_*.cfg`. The
   profile loads, `stats` reports the `format=` and `stride=` the cfg's
   own `captureFormat` and `captureCfg`/`phaseCaptureCfg` lines declare,
   and `used <= capacity`. The sensor is stopped again afterwards.

### readback (sensor: active, default profile)

1. `l3dump streams a valid dump`: `read_dump` returns bytes whose parsed
   header has `magic == ILD1`, `n_frames == plan pre + post`,
   `chirps_per_frame == n_tx * loops`, and the sensor is `active=1`
   afterwards.
2. `l3sparse returns every cell at the limit` (moved).
3. `l3sparse refuses an oversized request` (moved).
4. `l3sparse refuses a late request, then works` (moved).
5. `l3track without trackCfg is refused`: reply contains
   "needs trackCfg"; `freeze_req` unchanged; sensor still `active=1`.
6. `trackCfg validation`: wrong count, a negative value, a zero period, and
   a zero resolution are refused; a valid line replies `Done`.
7. `l3track streams the tracked cells`: after a realistic `trackCfg` (built
   from the loaded cfg's loop period and the range resolution the runtime
   uses), `read_tracked` returns a dump, a noise power, and an
   `OnboardTrack`; `freeze_done` increments by one; sensor `active=1`.

### trigger (sensor: active, default profile, hands-off)

1. `fresh session untriggered`: right after `send_config`, `stats` shows
   `phase=off latched=0 enabled=0`.
2. `triggerCfg validation` (moved): bad bin, negative power, non-integer
   hits, and wrong count are refused; a valid line replies `Done`.
3. `arming starts the detector`: arm with `FLOOR_PROBE_LEVEL`. Within
   `--wait-s`: `enabled=1`, `latched=0`, `pre_seen >= plan_pre`, the phase
   is no longer `off` or `no-frame`, and `tee > 0`.
4. `triggerCfg 0 0 0 disarms`: `enabled=0`, `phase=off`, `latched=0`.
5. `debugCfg streams parsable lines`: `debugCfg 1` writes a `trig` line in
   its own reply; that line parses with all eleven fields; `bin` and
   `level` echo the armed values; `debugCfg 0` stops the stream (no `trig`
   line in the following 0.5 s of port bytes); `debugCfg 2` is refused.
6. `debug lines only change on phase change`: with the lane empty and the
   detector armed above the floor, one second of port bytes contains no
   two consecutive lines with the same `phase`.
7. `floor measurement`: `measure_trigger_level` returns `floor > 0` and
   `level > floor`, and does not raise (no latch during the sample).
8. `reconfigure clears a previous arm`: arm, then `send_config`; the new
   session shows `enabled=0 latched=0 phase=off`.

### trigger-swing (sensor: active, default profile, `--swing`)

The section arms once at the measured level (or `--level`), then loops
`--shots` times (default 2). Each shot:

1. `shot N: ball on tee reaches watching`: prompt "Place a ball on the tee
   and press Enter". Within `--wait-s` the phase is `watching`.
2. `shot N: swing fires the trigger`: prompt "Swing, then wait". While
   waiting, `stats` is polled every 0.5 s so a notice must survive a
   command reply. On the notice: seconds from notice to `latched=1
   phase=fired` are reported; `freeze_req` rose by one; within `--wait-s`
   `freeze_done == freeze_req`.
3. `shot N: frozen ring reads back`: `read_tracked` (or `read_sparse` of
   every cell if `l3track` returns None) succeeds in under 1.0 s; the
   dump's frame count equals the plan; `pre_seen >= plan_pre`.
4. `shot N: host replay agrees`: `replay_dump` with the same bin, level,
   and hits fires on the ring.
5. `shot N: rearmed`: `latched=0`, `enabled=1`, `frames` advancing.

After the last shot:

6. `latched session is cleared by reconfigure`: one extra prompted swing
   whose ring is deliberately not read back, so the firmware sits at
   `latched=1`. `send_config` then yields `latched=0 enabled=0 phase=off`.
   This is the hardware run of the fix that stopped a dead host from
   leaving the radar "fired" forever.

Prompts, sleeps, and the clock are injected so unit tests script the whole
section without a port or a human.

### solve

1. `on-chip solve`: SKIP with "no CLI entry point in this firmware image".
   Replace with real checks when the MSS gains a solve command.

## Hardware-free tests

`tests/iwr6843_fakes.py` gains `ScriptedSerial`:

- A reply table `{command: bytes | Callable[[ScriptedSerial], bytes]}` so a
  reply can depend on how many times a command has been sent (for
  `frames` advancing, `freeze_done` catching up, `phase` progressing).
- `inject(bytes)` places bytes ahead of the next read, used to plant a
  `Triggered` notice inside a `stats` reply and to stream `trig` debug
  lines.
- Binary paths delegate to the existing `FakeSparseSerial`, `power_packet`,
  `slice_packet`, and the ILD1 builders used by `test_iwr6843_driver.py`.

`tests/test_iwr6843_firmware_checks.py` covers:

- `parse_stats` on all four real stats lines and `parse_trig` on a real
  debug line and a stats trig line; `StatsSnapshot` returns `None` for
  fields an older image omits.
- Every check with a passing script and at least one failing script:
  active stuck at 1 after stop, `enabled` stuck at 0, latch that never
  clears, a notice dropped by a command reply, a dump header that does not
  match the plan, `l3track` refused, an unknown command.
- Runner behaviour: `--only` order and unknown names, SKIP for
  `needs_swing` without `--swing`, `UnsupportedCommand` becomes SKIP, exit
  codes, `--fail-fast`, cleanup runs after a raising check, cleanup failure
  forces exit 1, JSON report shape.
- The whole swing section against a scripted port with a scripted prompt,
  including a timeout that turns into FAIL.
- A source-pinning test: every `tableEntry[n].cmd` in `l3_dump.c`'s CLI
  table that is not behind `ENABLE_HWA_SMOKE` appears in at least one
  check's command list, so a new firmware command without a check fails CI.

`test_iwr6843_fakes.py` continues to pin the fakes against the real driver.

## Documentation

`docs/iwr6843/verify.md` gets a "Firmware feature check" section before the
cadence soak: what the suite covers, the hands-off and `--swing` commands,
and what a SKIP for the solve means. `docs/development/firmware.md` adds a
"Validate the image" row pointing at the suite.

## Open questions

None. The hand-wave proxy was considered and rejected. The pytest-marker
structure was considered and rejected in favour of the repo's existing
script-plus-library pattern.
