# IWR-triggered kiosk reliability — spec

Base: `feat/iwr-calcs` at `776c266`. Reference branch: `weawer/feat/iwr-2ms-trigger-merge`
(`a6533a3`), fetched as remote `weawer`.

Goal: reliable IWR-triggered kiosk operation while preserving the onboard calculation
pipeline (ball-placement polling, onboard-result parsing, metric provenance, IWR observer →
OPS `request_capture()`).

First implementation: **stages 1–3**. Stages 4–6 follow later, each with its own plan.

## Stage 1 — Reproducible kiosk configuration

An explicit kiosk configuration ties together: firmware build identifier, capture profile and
sample format, calibration file, tee distance or explicit global tee bin, trigger mode (IWR
relay or independent OPS trigger), enabled onboard calculations. At startup, log the resolved
configuration and the actual firmware capabilities, and refuse incompatible combinations with
an actionable message. Keep `scripts/start-kiosk.sh`.

**Changed by the user on 2026-09-27:** weawer's `config/iwr6843_l3dump_adaptive_36f2ms_iq16.cfg`,
ported verbatim, is the **default** capture profile (`iq16-2ms`). The previous working profile
(wide 24-frame 3 ms IQ16) stays selectable as `wide-3ms` for comparison and recovery. The 2 ms
profile has not yet been through stage 6 qualification. Its cfg header says so, and it runs with
the firmware's default retention (no `captureCfg retain` line).

**Acceptance:** a session log identifies exactly which firmware, profile, calibration and
trigger settings produced its shots.

## Stage 2 — Host reliability

- **A. Range calibration on the trigger.**
  - `_self_trigger_config()` and `tee_global_bin()` use the calibration's range bias.
  - `--iwr6843-self-trigger-bin` stays an explicit (raw) override.
  - Trigger placement and measurement use one range convention.
  - Check that the corrected tee and the approach region are covered by the first
    processing window.
  - Follow mode never re-applies the bias to a bin the radar measured.
  - Tests: positive and negative bias, explicit override, window boundaries, follow mode.
- **B. No OPS sleep polling.**
  - Use bounded blocking reads.
  - Keep cancellation, fragmented-marker handling and completion detection.
  - Restore the original serial timeout on every exit path.
  - Keep serial ownership and locking.
- **C. Incomplete dumps.**
  - 8 s stall tolerance.
  - Log received versus expected bytes, and the elapsed time.
  - Reject incomplete captures as measurement inputs.
  - Keep failed-transfer evidence when diagnostic capture is on.
  - Recover to a known state before "ready".
  - Never retry `l3dump` expecting the same capture.

**Acceptance:** fragmented reads, stalls, cancellation and repeated captures neither deadlock
the kiosk nor silently produce valid-looking incomplete measurements.

## Stage 3 — One trigger owner per mode

| Mode | IWR | OPS |
|---|---|---|
| `iwr_primary` (default) | freeze, notify camera, send OPS `S!` | supply the speed capture when requested |
| `ops_independent` (diagnostic) | freeze, notify camera | triggers through ST/SM; never receives an IWR `S!` |
| `sound_gate` (existing, no IWR self-trigger) | GPIO edge | HOST_INT edge |

- Validate incompatible trigger settings at startup.
- Each accepted IWR event notifies each observer exactly once.
- Keep release/rearm for rejected, duplicate and disarmed events.
- Keep slow work out of the notification path.
- Test camera notification in both modes.

Record these timestamps separately:
- IWR notice
- OPS capture requested
- OPS capture observed (first byte)
- estimated impact (with its source and confidence)
- capture completed
- rearmed

Use monotonic time for host durations. Never subtract firmware ticks from host time.

**Acceptance:** one swing gives at most one accepted shot, OPS diagnostic mode never receives an
extra `S!`, and the camera is driven in both modes.

## Review decisions (2026-09-27, all recommended options chosen)

| # | Decision |
|---|---|
| 1A | Named JSON kiosk profiles in `config/kiosk/` (`iq16-2ms` is the default, `wide-3ms` is the fallback), selected with `--iwr6843-profile`. Explicit CLI flags override and are logged as overrides. The resolved config goes into `session_start`. The `experimental` flag is kept for future profiles; neither shipped profile sets it. |
| 2A | Firmware capability report: build id plus capability tokens, queried at startup. Firmware that can't report is an "unknown build" and may run non-experimental profiles only. (The CLI table is full, so this ships as the sub-mode `stats caps`.) |
| 3A | One calibration-owned range mapping. `tee_global_bin` takes `range_bias_m` as a **required** keyword. Startup checks watch-region coverage. Follow mode is never re-biased. The explicit bin stays raw. |
| 4A | Explicit `TriggerMode`. The relay observer is registered first and only in `iwr_primary`. `S!` is written under an OPS serial lock shared with rearm and read-reset, gated by an "armed" flag. Each event gets a monotonic timeline. |
| 5A | One shared OPS dump reader for `trigger_capture` and `wait_for_hardware_trigger`. It uses bounded blocking reads, always sets and restores the timeout (even when it was `None`), checks cancel on every loop, and returns a structured `DumpRead`. |
| 6A | Typed `IncompleteDumpError`, with the stall tolerance taken from the profile (default 8 s). Partial dumps are saved in diagnostic mode. The OPS reader checks the sample count. No `l3dump` retry. |
| 7A | Recovery routine: drain until quiet, health check, then release/rearm with bounded attempts. The radar is marked armed only after this succeeds; otherwise `needs_attention` is set. |
| 8A | `time.monotonic()` for every duration and deadline in the files touched. Wall-clock time only for record timestamps. |
| 9A | Shared `tests/serial_fakes.py` with `VirtualSerial` and `FakeClock`, modelling pyserial timeout semantics. |
| 10A | Mode × camera × event-kind matrix, a barrier-driven `S!`/rearm race test, and a timeline test. |
| 11A | Exhaustive synthetic fragment and stall cases, plus a seeded, chunked replay of an in-repo dump. No new binaries. |
| 12A | Pure `resolve_kiosk_config` / `check_firmware_compat` with table-driven tests, plus thin wiring tests. |
| 13A | Incremental completion scan in the OPS reader. Scan cost stays linear. |
| 14A | IWR capture deque bounded by age and count. Dropped captures are logged as `iwr_capture_unmatched`. |
| 15A | Log the CPU governor into `session_start` and warn when it isn't `performance`. Port `set_cpu_governor.sh` as a manual tool. Warn on IWR-notice→`S!` latency. |
