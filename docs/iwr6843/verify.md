---
icon: lucide/play
---

# Start and Verify

Bring the system up with the geometry you measured, then confirm the first
capture looks right before hitting a full session.

## Start OpenFlight

For the first run, use `--debug`. This retains each TI dump for inspection and
offline replay. This example uses the Option A GPIO UART path. Replace the
example geometry with your measurements:

```bash
scripts/start-kiosk.sh --debug \
  --radar-port /dev/ttyAMA0 \
  --iwr6843 \
  --iwr6843-port /dev/ttyUSB0 \
  --iwr6843-config config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg \
  --iwr6843-tee-m 1.575 \
  --iwr6843-net-m 4.6 \
  --iwr6843-tilt-deg 10.4 \
  --iwr6843-radar-height-m 0.1524 \
  --iwr6843-ball-height-m 0.040 \
  --session-location home
```

For Option B, replace `/dev/ttyAMA0` after `--radar-port` with the OPS USB serial
device, preferably its stable `/dev/serial/by-id/...` path.

The example uses the recommended wide profile. To test the 2 ms profile with
the 54 ms ball phase, change only the config argument to:

```text
--iwr6843-config config/iwr6843_l3dump_dense_45f2ms_53bin_iq8.cfg
```

To test dense sampling while retaining the wide profile's late-flight window,
use the experimental profile:

```text
--iwr6843-config config/iwr6843_l3dump_dense_36f2ms_53bin_iq8_wide_late.cfg
```

Passing `--iwr6843-config` explicitly keeps the selected profile visible in the
launch command and session log.

The OPS port can also be supplied as `--ops-port /dev/ttyAMA0`. `--port` means
the web-server port, so do not use it for the OPS serial device.

The TI port can be omitted after the custom firmware is running; OpenFlight
probes available USB serial ports for the expected CLI. Supplying
`--iwr6843-port` is clearer during initial setup and avoids ambiguity when
multiple USB serial devices are connected.

Once the setup is stable, remove `--debug` for normal operation. The server
still processes TI captures in memory, but it does not write a dump for
every shot. Session JSONL entries only contain a dump path when debug capture
is enabled.

## Verify The First Capture

Healthy startup includes messages similar to:

```text
[IWR6843] Configured on BCM17 using /dev/ttyUSB0 (..., waiting for OPS)
[IWR6843] Armed on BCM17
[SERVER] IWR6843 initialized (... firmware boundary freeze)
```

Use one clap to verify the shared trigger and dump transfer. A clap is not a
golf ball, so `rejected_by_ball_tracker` is expected. The important result is a
complete capture:

```text
[IWR6843] Trigger #1: dumping firmware-frozen L3 ring
[IWR6843] Capture #1 complete: 732812 bytes
```

Firmware health should show an active sensor, increasing frame/wrap counters,
and no RF faults:

```text
active=1 ... rf_faults=0
```

Then hit a ball. A trusted result logs `Angle source: radar`. A shot may still
appear in the UI with an estimated angle when the TI capture completes but the
ball track does not meet the acceptance gates.

In debug mode, verify that the session contains an `iwr6843_capture` entry, a
`temperature_report` object, and a `capture_path` pointing to the saved
`.l3dump` file.

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
| `readback` | `l3dump`, `l3sparse` (limit, oversized, late request), `trackCfg`, `l3track`, and `l3release` stream and rearm | yes |
| `trigger` | a fresh session is untriggered, `triggerCfg` arms and disarms, the detector goes live only once the pre-trigger ring is full, `debugCfg` streams parsable change-only lines, the floor measurement works, and reconfiguring clears a previous arm | yes |
| `trigger-swing` | with `--swing`: a ball on the tee reaches `watching`, a swing fires `Triggered` (the notice must survive a `stats` reply), the frozen ring reads back in under 1.0 s, the host detector replay agrees, the ring rearms, and a latched session is cleared by reconfigure | no, prompts you |
| `solve` | always `SKIP`: the on-chip DSS solve is in the image but the MSS exposes no CLI entry point for it yet | yes |

The `l3track without trackCfg` check can only prove the refusal on the first run
after a power cycle; on later runs it reports `SKIP` (the firmware never clears
`gTrackConfigured`, so `l3track` streams instead of refusing).

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

## Cadence Acceptance Soak

This is the acceptance gate for any change to the capture-path DMA/CPU memory
layout (for example, moving a scratch buffer between L3 and `DATA_RAM`). It
runs the sensor for tens of thousands of frames and fails on any sign that
the firmware could not keep up with the inter-frame budget:

```bash
uv run python scripts/hardware-test/iwr6843_cadence_soak.py \
    --config config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg --frames 50000
uv run python scripts/hardware-test/iwr6843_cadence_soak.py \
    --config config/iwr6843_l3dump_dense_51f2ms_53bin_iq8.cfg --frames 50000
```

Run it against **both** shipped profiles — the wide/iq16 default and the
dense/iq8 profile — since they run the same firmware image at different
frame periods (3 ms and 2 ms respectively, read automatically from each
`.cfg`'s `frameCfg` line).

The script reads the firmware's `stats` CLI response
(`firmware/iwr6843/l3_dump.c`, `l3_cli_stats`) and checks:

- `hwa_frames` reached at least 90% of the requested `--frames` (a low count
  means the sensor stalled or `sensorStop` cut the run short, not that the
  cadence held).
- `hwa_missed` / `hwa_frames` (the HWA frame-start miss rate) does not exceed
  twice the recorded baseline of 0.0089% for the shipped profile. Doubling
  the baseline is a materiality band: DMA/CPU contention from a bad
  relocation shows up as a large jump, not a rate that hovers just above the
  baseline.
- `iq8_overrun` (IQ8 pack overruns) is zero.
- `iq8_edma_err` (IQ8 EDMA errors) is zero.

**Pass criteria:** the script prints `PASS` and exits 0 for both profiles.
**A failure means the relocation broke the inter-frame budget on real
silicon — revert to the L3 fallback rather than tuning around it.**

The stats parser (`parse_stats` in the script) is unit-tested without
hardware in `tests/test_iwr6843_monitor.py`
(`test_cadence_soak_parses_firmware_stats` and related tests), against the
exact field names the firmware emits.

### Readback measurement (manual, alongside the soak)

While soaking the dense/iq8 profile, take at least 20 shots and record:

- `l3track` readback latency — must stay under 1.0 s per shot.
- Frequency of `l3sparse` truncation warnings (`monitor.py:510`) — must be no
  more frequent than on the 45-frame profile.

Record both numbers alongside the soak's PASS/FAIL output when reporting
results for a DATA_RAM relocation change.
