# IWR6843 Operator Guide

This guide covers the supported OpenFlight setup for the TI IWR6843LEVM. It
starts with an OpenFlight-ready Raspberry Pi and an unconfigured radar, then
walks through wiring, firmware flashing, mounting, measurement, startup,
verification, calibration, and offline replay.

The production system uses two radars:

| Device | Responsibility |
|---|---|
| OPS243 | Sound-triggered shot detection, ball speed, and club speed |
| IWR6843 | Short-window radar capture, vertical launch angle, and experimental horizontal direction |

The sound detector sends the same impact edge to both systems. The OPS243
freezes its rolling buffer directly. The Raspberry Pi receives that edge on
BCM17 and immediately asks the IWR6843 firmware to finish and dump its rolling
frame ring.

For firmware development, architecture, and build instructions, see
[`firmware/README.md`](../../firmware/README.md).

## Current Validated Configuration

Use these files together. Mixing a firmware binary and config from different
variants will fail startup or produce the wrong capture geometry.

| Component | Current file or value |
|---|---|
| Firmware | `firmware/releases/l3_dump_vTX2_hwa_window53_12loops_18frames_4ms_v2.bin` |
| Radar config | `config/iwr6843_l3dump_vTX2_window53_12l18f.cfg` |
| Reference array calibration | `config/iwr6843_calibration_reference.json` |
| Transmitters / receivers | 3 TX / 4 RX |
| Capture | 12 loops, 18 frames, 4 ms frame spacing |
| Trigger split | 6 pre-trigger frames, 12 post-trigger frames |
| Saved range data | 53 complex range bins per frame with moving early/middle/late windows |
| Complete dump size | 549,542 bytes, including header and per-frame window metadata |

The validated v2 firmware SHA-256 is:

```text
3045bb2f087b40c228bf1dd5190cf3fac6dbde50682c7927e86714314b0e7fcb
```

On the Pi, verify the checked-in image with:

```bash
sha256sum firmware/releases/l3_dump_vTX2_hwa_window53_12loops_18frames_4ms_v2.bin
```

## Before You Start

You need:

- A Raspberry Pi running OpenFlight.
- A TI IWR6843LEVM and a data-capable USB cable.
- An OPS243 radar connected through either the Pi GPIO UART or a separately
  powered USB hub.
- A configured SparkFun SEN-14262 sound detector or the equivalent supported
  trigger. Complete the [sound-trigger wiring guide](../sound-trigger-wiring.md)
  first.
- A stable Pi power supply and stable power for every USB-connected radar.
- Access to the IWR6843 boot-mode switch and RESET button.
- Measurements for radar-to-ball distance, radar-to-net distance, radar height,
  ball height, and radar tilt.

Run all commands from the OpenFlight repository root unless a section says
otherwise.

## Connect The Hardware

Power the system off before changing GPIO wiring.

### Power And Data Layout

The supported connection depends on the OPS243-A variant. Do not power both
radars from an unpowered, bus-powered USB hub.

#### Option A: OPS Through The Pi GPIO UART (Non-WiFi OPS Only)

The validated layout keeps the TI board on USB and connects the OPS243 to the
Pi UART header for power and data.

If the OPS243 is currently on USB, migrate and validate it on its own before
adding the TI board — see
[Moving the OPS243 from USB to the Pi GPIO UART](../ops243-uart-migration.md).
Doing both at once makes any failure ambiguous.

> [!WARNING]
> Do not use this option with a WiFi-equipped OPS243-A. The onboard WiFi module
> already drives the radar processor's UART receive line, so J3 pin 6 cannot
> accept API commands from the Pi. J3 pin 7 can expose transmit data, but
> receive-only UART is not sufficient for OpenFlight because the server must
> configure and rearm the OPS after every capture. Use Option B instead.

| Connection | Wiring | Purpose |
|---|---|---|
| IWR6843 | USB to Pi or stable powered hub | Power, CLI commands, and binary L3 dump transfer |
| OPS power | Pi 5V physical pin 2 or 4 to OPS J3 pin 9 (`5V`) | Powers the OPS without sharing the TI USB path |
| OPS ground | Pi GND to OPS J3 pin 10 (`GND`) | Establishes the shared electrical reference |
| OPS data to Pi | OPS J3 pin 7 (`TxD`) to Pi GPIO15 / physical pin 10 (`RXD0`) | OPS transmits readings into Pi RX |
| Pi commands to OPS | Pi GPIO14 / physical pin 8 (`TXD0`) to OPS J3 pin 6 (`RxD`) | Pi transmits commands into OPS RX |
| Sound trigger | Detector `GATE` to OPS J3 pin 3 (`HOST_INT`) and Pi BCM17 / physical pin 11 | Freezes OPS and notifies the Pi of the same impact |
| Trigger power | Pi 3.3V and GND to detector `VCC` and `GND` | Keeps the trigger at Pi-safe logic levels |

#### Option B: OPS Through USB

The OPS243 can remain connected over USB, but the hub must have its own external
power input and must be powered separately instead of drawing all radar power
from the Pi. One option is the
[Acer four-port powered USB hub](https://www.amazon.com/dp/B0CN3F9Y1Z).
This is the recommended connection for a WiFi-equipped OPS243-A.

With this layout, connect both radar USB cables to the externally powered hub.
Do not also connect the OPS 5V, RX, or TX pins to the Pi GPIO header. The shared
sound-trigger GATE connection to OPS `HOST_INT` and Pi BCM17 is still required.

#### Pi Header Reference

| Physical pin | BCM name | Use |
|---|---|---|
| Pin 2 or 4 | 5V | OPS power |
| Pin 6, 9, 14, 20, 25, 30, 34, or 39 | GND | Shared ground |
| Pin 8 | GPIO14 / TXD0 | Pi TX to OPS RX |
| Pin 10 | GPIO15 / RXD0 | Pi RX from OPS TX |
| Pin 11 | GPIO17 | Sound-trigger GATE input |

#### OPS243-A J3 Header Reference

Use the 10-pin header labeled `J3` on the OPS243-A. Confirm the pin-1 marker or
board silkscreen before connecting wires; do not infer pin numbering from which
side of the board is closest.

| J3 pin | OPS signal | Connect to |
|---|---|---|
| Pin 3 | `HOST_INT` / rolling-buffer trigger | Sound detector `GATE` and Pi BCM17 / physical pin 11 |
| Pin 6 | `RxD` (input to non-WiFi OPS only) | Pi GPIO14 / `TXD0` / physical pin 8 |
| Pin 7 | `TxD` (output from OPS) | Pi GPIO15 / `RXD0` / physical pin 10 |
| Pin 9 | `5V` | Pi 5V physical pin 2 or 4 |
| Pin 10 | `GND` | Any Pi GND pin used by the shared ground |

UART transmit and receive are intentionally crossed: the OPS `TxD` output goes
to the Pi `RXD0` input, and the Pi `TXD0` output goes to the OPS `RxD` input.
This bidirectional mapping applies only when the OPS does not contain the WiFi
module described above.
The pin assignments come from the
[OPS243 datasheet](https://omnipresense.com/wp-content/uploads/2019/03/OPS-DS-003-0.1_OPS243.pdf);
the use of J3 pin 3 as a trigger is defined by
[AN-027 OPS243-A Rolling Buffer](https://omnipresense.com/wp-content/uploads/2025/06/AN-027-A_Rolling-Buffer.pdf).

The GATE signal is a three-way electrical connection. Splice three jumper wires
together at one junction: one from the sound detector `GATE`, one to the OPS243
J3 pin 3 (`HOST_INT`), and one to Pi BCM17 / physical pin 11. Use a soldered and
insulated splice or a secure three-way connector; do not rely on loosely
twisted wires.

```text
Sound detector GATE
  +-- OPS243 J3 pin 3 (HOST_INT)
  +-- Pi BCM17 / physical pin 11

Sound detector VCC
  +-- Pi 3.3V

Sound detector GND
  +-- Pi GND, shared with OPS J3 pin 10 (GND)
```

Important electrical rules:

- With Option A, cross serial TX and RX. OPS `TX` connects to Pi `RX`; OPS `RX`
  connects to Pi `TX`.
- Never connect 5V to a Pi GPIO signal pin.
- Confirm that the OPS serial interface uses 3.3V TTL signaling. Do not connect
  RS-232 voltage levels to Pi GPIO.
- Power the sound detector from Pi 3.3V so its GATE output remains Pi-safe.
- Keep Pi, OPS, and trigger grounds connected.
- Treat intermittent USB disconnects and simultaneous radar failures as power
  problems first.

## Prepare The Raspberry Pi UART

Complete this section only when using Option A. If the OPS243 is connected over
USB through an externally powered hub, skip this section and continue to
**Prepare Serial And GPIO Permissions**.

Enable the Pi hardware UART and remove the Linux login console from it:

```bash
sudo raspi-config
```

Choose `Interface Options` -> `Serial Port`, then answer:

1. Disable the login shell over serial.
2. Enable the serial-port hardware.
3. Reboot the Pi.

On Raspberry Pi 5, physical pins 8 and 10 use UART0 at `/dev/ttyAMA0`. Verify
that device:

```bash
ls -l /dev/ttyAMA0
```

Do not use `/dev/serial0` for this wiring on Raspberry Pi 5: it normally points
to `/dev/ttyAMA10`, which is the separate debug-header UART rather than the
40-pin GPIO header.

If `/dev/ttyAMA0` is missing, confirm that UART0 is enabled:

```bash
grep enable_uart /boot/firmware/config.txt /boot/config.txt 2>/dev/null
```

At least one boot configuration should contain:

```text
enable_uart=1
```

## Prepare Serial And GPIO Permissions

Both connection options require serial-device access for the radars and GPIO
access for the shared trigger. Confirm that the OpenFlight user belongs to
`dialout` and `gpio`:

```bash
groups
```

If either group is missing:

```bash
sudo usermod -a -G dialout,gpio "$USER"
sudo reboot
```

## Prepare The Sound-Trigger GPIO

No `raspi-config` interface setting is required for the sound-trigger input.
OpenFlight uses BCM17 by default, which is physical pin 11 on the Pi header. The
checked-in Python dependencies include `gpiozero` and the Pi `lgpio` backend.

The launch command does not need `--iwr6843-trigger-pin` when the GATE splice is
wired to BCM17. If startup reports `GPIO busy`, another OpenFlight, calibration,
or shot-test process still owns the pin; stop that process before retrying.

## Identify The TI Serial Port

Connect the IWR6843LEVM to the Pi over USB and inspect the serial devices:

```bash
ls -l /dev/serial/by-id/
ls -l /dev/ttyUSB*
```

The board's CP2105 exposes two UART interfaces. OpenFlight firmware uses the
**Enhanced/UARTA** interface for both CLI commands and binary dumps. This is
normally USB interface `00` and `/dev/ttyUSB0`. Do not select the Standard/data
interface, which is normally interface `01` and `/dev/ttyUSB1`.

The exact `/dev/ttyUSB*` number can change after reconnecting hardware. Prefer
the corresponding `/dev/serial/by-id/...-if00-port0` path when available. The
examples below use `/dev/ttyUSB0`; replace it if your Enhanced interface has a
different path.

When using Option B, also identify the OPS243 USB serial path under
`/dev/serial/by-id/`. Use that stable path for `--radar-port` instead of relying
on a changing `/dev/ttyACM*` number.

## Flash The IWR6843 Firmware

The recommended method flashes directly from the Pi using the checked-in ROM
bootloader client. TI UniFlash is a fallback, not a requirement.

### 1. Stop Serial Users

Stop OpenFlight, calibration, `shot_test.py`, and any other process using the TI
port. Use `Ctrl+C` in the terminal that launched OpenFlight, then check for
remaining owners:

```bash
pgrep -af 'openflight|calibrate|shot_test'
sudo fuser -v /dev/ttyUSB0
```

Do not flash until the Enhanced UART is free.

### 2. Enter Flash Mode

Set the IWR6843LEVM boot switches to:

```text
S1.1 ON, S1.2 OFF, S1.3 ON, S1.4 ON, S1.5 OFF
```

Do not press RESET yet. The flashing script opens the UART first and tells you
when to reset the board.

### 3. Probe The ROM Bootloader

Run the non-destructive probe:

```bash
uv run python firmware/flash_iwr6843.py \
  --probe \
  --port /dev/ttyUSB0
```

Follow the prompts exactly:

1. Type `READY` so the script opens the UART and settles its control lines.
2. Press and release RESET only when the script asks.
3. Wait one second.
4. Type `PROBE`.

The expected result is:

```text
IWR6843 ROM bootloader handshake: PASS
```

If you are flashing immediately, leave the board in flash mode. The flash
command will ask for another RESET after it opens the UART.

### 4. Flash The Validated Image

```bash
uv run python firmware/flash_iwr6843.py \
  firmware/releases/l3_dump_vTX2_hwa_window53_12loops_18frames_4ms_v2.bin \
  --port /dev/ttyUSB0
```

Follow the `READY` -> RESET -> one-second wait -> `FLASH` sequence shown by the
script. The default operation erases the existing serial flash, writes the
image in acknowledged chunks, closes it, and asks the ROM bootloader to verify
the result.

A successful flash ends with:

```text
Erasing existing SFLASH...
Opening firmware image...
Writing firmware...
Writing: 100% (.../... bytes)
Closing and verifying firmware...

Flash verified by the IWR6843 ROM bootloader.
```

Do not reset, disconnect, or remove power while the erase or write is active.
An erase can take longer than ten seconds.

### 5. Return To Functional Mode

Set the switches to:

```text
S1.1 OFF, S1.2 OFF, S1.3 ON, S1.4 ON, S1.5 OFF
```

Press and release RESET. The custom firmware is now ready for OpenFlight.

## Mount And Aim The Radar

Mount the radar behind the ball with the antenna face pointing down the target
line. The validated enclosure rotates the board so its vertical virtual array
is physically vertical, with the TX antennas above the RX antennas.

The IWR6843 can start at approximately the same upward tilt as the OPS243,
typically around 10 degrees, when both antenna faces are mounted parallel. Treat
10 degrees as a mounting starting point, not a universal calibration value.
Measure the IWR6843 antenna-face tilt independently and enter that measured
value in OpenFlight.

Start with the IWR6843 antenna center approximately 6 inches (`0.1524 m`) above
the floor surface under the radar. Measure vertically from that surface to the
center of the antenna array, not to the enclosure bottom or mounting feet. This
is the validated starting height, not a substitute for entering the actual
measured height.

Mounting requirements:

- Aim the antenna face toward the intended start line, not diagonally across
  the hitting area.
- Keep the antenna face unobstructed.
- Keep the board rotation consistent with the validated enclosure.
- Use a rigid mount. Small mechanical shifts can appear as angle bias.
- Measure tilt against the antenna face or a known-parallel enclosure surface.
- Re-measure after moving to a different floor, mat, bay, or stand.

A corner reflector placed on the target line can verify horizontal aim. It is
useful for alignment and static health checks, but it does not replace moving
golf-ball validation.

## Measure The Geometry

OpenFlight needs these physical inputs:

| Argument | Measurement |
|---|---|
| `--iwr6843-tee-m` | Slant distance from antenna center to ball center |
| `--iwr6843-net-m` | Distance from antenna center to net or screen |
| `--iwr6843-tilt-deg` | Antenna-face mount tilt from an inclinometer |
| `--iwr6843-radar-height-m` | Antenna-center height above the floor reference |
| `--iwr6843-ball-height-m` | Ball-center height above the same floor reference |

Measurement guidance:

- Measure from the antenna center, not the enclosure edge or mounting feet.
- Use radar-to-ball slant range for `tee-m`.
- Keep `net-m` honest so late net reflections can be excluded.
- Measure radar and ball height from the same floor reference. If the radar and
  ball sit on different surfaces, extend a common level reference between them.
- Add an elevated mat to ball height. A 1 inch mat adds approximately `0.0254 m`.
- A typical iron ball center is around `0.040 m`; a driver tee is higher.
- Do not reuse a tilt value after moving the rig unless you verify it again.

The checked-in reference calibration contains the array correction used by the
validated radar. It provides a known starting point, not a universal factory
calibration. The operator calibration session below checks geometry and
estimator consistency; it does not regenerate the file's per-element complex
array correction. A different radar board or antenna orientation may require a
new corner-reflector array calibration before source-of-truth accuracy can be
expected.

## Start OpenFlight

For the first run, use `--debug`. This retains each TI dump for inspection and
offline replay. This example uses the Option A GPIO UART path. Replace the
example geometry with your measurements:

```bash
scripts/start-kiosk.sh --debug \
  --radar-port /dev/ttyAMA0 \
  --iwr6843 \
  --iwr6843-port /dev/ttyUSB0 \
  --iwr6843-config config/iwr6843_l3dump_vTX2_window53_12l18f.cfg \
  --iwr6843-tee-m 1.575 \
  --iwr6843-net-m 4.6 \
  --iwr6843-tilt-deg 10.4 \
  --iwr6843-radar-height-m 0.1524 \
  --iwr6843-ball-height-m 0.040 \
  --session-location home
```

For Option B, replace `/dev/ttyAMA0` after `--radar-port` with the OPS USB serial
device, preferably its stable `/dev/serial/by-id/...` path.

The production config and reference calibration above are the server defaults.
Passing `--iwr6843-config` explicitly is still useful when diagnosing a setup
because the selected file is visible in the launch command and session log.

The OPS port can also be supplied as `--ops-port /dev/ttyAMA0`. `--port` means
the web-server port, so do not use it for the OPS serial device.

The TI port can be omitted after the custom firmware is running; OpenFlight
probes available USB serial ports for the expected CLI. Supplying
`--iwr6843-port` is clearer during initial setup and avoids ambiguity when
multiple USB serial devices are connected.

Once the setup is stable, remove `--debug` for normal operation. The server
still processes TI captures in memory, but it does not write a 549 KB dump for
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
[IWR6843] Capture #1 complete: 549542 bytes
```

Firmware health should show an active sensor, increasing frame/wrap counters,
and no RF faults:

```text
active=1 ... rf_faults=0
```

Then hit a ball. A trusted result logs `Angle source: radar`. A shot may still
appear in the UI with an estimated angle when the TI capture completes but the
ball track does not meet the acceptance gates.

In debug mode, verify that the session contains an `iwr6843_capture` entry and
that its `capture_path` points to the saved `.l3dump` file.

## Run A Calibration Session

Stop the kiosk before running calibration so it releases BCM17 and both serial
ports. The calibration command uses the same OPS trigger, OPS processing, TI
capture, and LCMF estimator as the server.

```bash
GPIOZERO_PIN_FACTORY=lgpio uv run \
  --with gpiozero \
  --with lgpio \
  python scripts/iwr6843/calibrate.py \
  --shots 20 \
  --club 7i \
  --ops-port /dev/ttyAMA0 \
  --iwr6843-port /dev/ttyUSB0 \
  --cfg config/iwr6843_l3dump_vTX2_window53_12l18f.cfg \
  --tee-m 1.575 \
  --net-m 4.6 \
  --tilt-deg 10.4 \
  --radar-height-m 0.1524 \
  --ball-height-m 0.040
```

Add `--debug` when you want raw dumps for offline replay. Calibration output is
written under `~/openflight_sessions/iwr6843_calibration/<timestamp>/` unless
`--outdir` is supplied.

The terminal reports:

- OPS ball and club speed.
- TI launch angle or rejection reason.
- Track RMS and inlier count.
- Estimated ball-start range.
- A radar-consistency tilt candidate.

The tilt candidate is not source-of-truth launch-angle calibration. It shows
the mount tilt where the LCMF component models agree best and can flag a large
setup error. Final geometry should still come from physical measurement and,
when available, launch-monitor source-of-truth validation.

Use `--no-tilt-sweep` when you only need capture diagnostics and faster shot
turnaround.

## Replay Saved Captures

Offline replay reruns LCMF without hardware and is the safest way to compare
estimator changes against identical radar data.

Replay a debug session JSONL:

```bash
uv run python scripts/iwr6843/replay.py \
  --input ~/openflight_sessions/session_YYYYMMDD_HHMMSS_home.jsonl \
  --cfg config/iwr6843_l3dump_vTX2_window53_12l18f.cfg \
  --tee-m 1.575 \
  --net-m 4.6 \
  --tilt-deg 10.4 \
  --radar-height-m 0.1524 \
  --ball-height-m 0.040 \
  --club 9i \
  --out replay.csv
```

Replay one dump:

```bash
uv run python scripts/iwr6843/replay.py \
  --input ~/openflight_sessions/iwr6843/shot.l3dump \
  --ball-speed-mph 105.9 \
  --cfg config/iwr6843_l3dump_vTX2_window53_12l18f.cfg \
  --club 9i \
  --tee-m 1.575 \
  --net-m 4.6 \
  --tilt-deg 10.4 \
  --radar-height-m 0.1524 \
  --ball-height-m 0.040
```

A session JSONL can only replay TI captures saved while `--debug` was active. A
standalone dump needs `--ball-speed-mph` because OPS speed is not stored in the
TI binary dump.

## Troubleshooting

Start with the symptom shown in the terminal. Avoid changing estimator settings
until power, ports, firmware, config, and geometry are verified.

| Symptom | Likely cause | Action |
|---|---|---|
| `no IWR6843 CLI found` | Wrong USB interface, board still in flash mode, missing functional RESET, stale serial owner, or unstable power | Set functional switches, press RESET, verify interface `00`, stop serial processes, then retry with explicit `--iwr6843-port` |
| `GPIO busy` | Another kiosk, calibration, or shot-test process owns BCM17 | Stop the old process; use `pgrep -af` and `sudo fuser -v /dev/gpiochip*` to locate it |
| Config rejected at startup | Flashed firmware and `.cfg` geometry do not match | Flash the v2 image and use `iwr6843_l3dump_vTX2_window53_12l18f.cfg` together |
| Bootloader probe returns no response | Wrong CP2105 port or RESET occurred before the script opened UART | Use Enhanced/UARTA, rerun the probe, type `READY`, then RESET only when prompted |
| Flash fails after `Erasing existing SFLASH` | Transfer was interrupted after the old image was erased | Leave the board in flash mode and rerun the complete flash; the ROM bootloader is still available |
| Server starts only after unplugging TI | Board was not reset cleanly, a prior dump was still streaming, or USB/power wedged | Stop the old process, press RESET in functional mode, wait for the port, then reconnect USB only if needed |
| `short IWR6843 dump` | Interrupted UART transfer, process shutdown during dump, or wrong firmware format | Let the active dump finish, restart, and confirm the expected 549,542-byte capture |
| Clap produces `rejected_by_ball_tracker` | A clap has no moving ball range track | Expected for trigger testing; confirm the dump completed, then hit a ball |
| `rejected_track_quality` | A ball-like track was found but it was too thin, noisy, inconsistent, or net-contaminated | Verify geometry and aim; inspect the debug dump before relaxing acceptance gates |
| `rejected_missing_tdm_sign` | The ball track was usable, but the TX timing evidence did not resolve a trustworthy correction sign | Keep the estimated UI angle, inspect the debug dump, and verify signal quality before changing gates |
| All UI angles are estimated | TI captures are absent, unmatched to OPS, or rejected by LCMF | Run with `--debug`, inspect `iwr6843_capture`, and check the reported rejection reason |
| OPS reports no data | Wrong OPS port, missing power, WiFi OPS connected through unsupported receive-only J3 UART, or non-WiFi UART wired incorrectly | For a WiFi OPS use the externally powered USB hub; otherwise verify `/dev/ttyAMA0`, power, shared ground, and crossed TX/RX |
| Either radar disconnects when both run | Insufficient USB power or unstable cabling | Use OPS GPIO power or a hub with its own external supply; verify the hub supply is connected and sized for both radars |
| Angles are consistently shifted | Tilt, antenna orientation, radar height, ball height, or tee distance is wrong | Re-measure all geometry from the antenna center and common floor reference |
| Dump file is missing from the session | OpenFlight was not launched with `--debug` | Re-run in debug mode when raw capture retention is required |

If the firmware itself must be rebuilt rather than flashed from the checked-in
binary, continue with [`firmware/README.md`](../../firmware/README.md).
