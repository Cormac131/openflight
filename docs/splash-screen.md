# Startup Splash Screen

OpenFlight can open a local splash screen immediately after a kiosk launch so
the operator can see that a desktop tap was accepted while software and radar
hardware initialize. Enable it with `--startup-splash`:

```bash
scripts/start-kiosk.sh --startup-splash
```

The splash is opt-in. Without the flag, `scripts/start-kiosk.sh` retains its
existing startup behavior.

![OpenFlight starting the server and TI radar](assets/startup-splash-ti-loading.png)

## What It Shows

The component list is created from the active startup configuration before the
browser opens. OpenFlight server and the OPS radar are always first. Optional
hardware appears only when its corresponding command-line option is enabled.

| State | Meaning |
|---|---|
| Waiting | Configured, but its initialization step has not started |
| Starting | OpenFlight is actively initializing the component |
| Ready | Initialization completed successfully |
| Skipped | An optional component was unavailable and startup can continue |
| Error | Startup stopped and the recovery panel explains the next action |

The display order is intentionally independent of initialization order. The
existing synchronous hardware startup remains unchanged; the splash only makes
its boundaries visible.

When the OpenFlight server responds, the same Chromium window automatically
loads the main UI. The splash and its status endpoint bind only to loopback and
do not require internet access.

## Recommended Raspberry Pi Launcher

Most users can continue running `scripts/start-kiosk.sh` directly. A user-local
wrapper is recommended for a touchscreen installation because it can preserve
that Pi's device paths, measured geometry, camera calibration, and optional
hardware flags without placing machine-specific values in Git.

Install the example wrapper and terminal-free desktop entry:

```bash
cd ~/openflight
scripts/setup/install_desktop_launcher.sh
```

The installer:

- Creates `~/run-openflight.sh` from
  `scripts/setup/run-openflight.example.sh` only when the local file is absent.
- Preserves an existing `~/run-openflight.sh` on every subsequent run.
- Creates or refreshes `~/Desktop/OpenFlight.desktop` for the current user.
- Uses `Terminal=false` and does not invoke `lxterminal`.
- Makes the launcher executable and asks the Pi desktop to trust it.
- Points the desktop entry at the local wrapper rather than directly at the
  repository startup script.

Edit the local wrapper after its first installation:

```bash
nano ~/run-openflight.sh
```

Add only the options for hardware installed on that Pi. For example:

```bash
openflight_args=(
    --startup-splash
    --iwr6843
    --iwr6843-port /dev/serial/by-id/REPLACE_WITH_TI_SERIAL_ID
    --iwr6843-tee-m 1.372
    --iwr6843-net-m 4.064
    --iwr6843-tilt-deg 5.5
    --iwr6843-radar-height-m 0.229
    --iwr6843-ball-height-m 0.021
    --battery geekworm
)
```

The geometry above is an example, not a default. Follow the
[IWR6843 operator guide](iwr6843/README.md#measure-the-geometry) and enter the
measurements from the actual installation.

The example wrapper also holds a per-user launch lock. Repeated taps exit
without creating another process that could compete for the OPS, TI, camera, or
GPIO hardware.

## Updating An Existing Pi

After pulling a version that includes the splash:

```bash
cd ~/openflight
git pull
uv sync
scripts/setup/install_desktop_launcher.sh
```

The final command replaces an older desktop entry that opens a terminal while
preserving the user's `~/run-openflight.sh`. No reboot is required. Close an
existing OpenFlight session first, then launch the refreshed desktop icon.

Raspberry Pi desktop settings determine whether icons require a single click or
a double click. The installer removes the separate “execute or execute in
terminal” choice; it does not change the user's global file-manager click
preference.

### Optional Faster Repeat Launches

`start-kiosk.sh` normally lets `uv` synchronize the environment, which makes an
updated checkout safe to launch. After running `uv sync`, a local wrapper may
skip that repeated work:

```bash
export OPENFLIGHT_UV_RUN_ARGS=--no-sync
```

Run `uv sync` again after pulling a dependency change. Camera installations
that rely on Raspberry Pi OS's system Picamera2 package may also require the
following local setting:

```bash
export UV_PYTHON=/usr/bin/python3
```

Do not add `GPIOZERO_PIN_FACTORY=lgpio`. OpenFlight selects the Raspberry Pi 5
GPIO chip explicitly; the environment override enters gpiozero's broken
auto-detection path instead.

## Startup Errors

The error screen identifies the component, gives the shortest known recovery
action, and retains the terminal-log location for diagnosis.

| Failure | Operator guidance |
|---|---|
| OPS unavailable | Check OPS USB and power connections, then relaunch |
| TI unavailable | Check TI USB and power connections, then relaunch |
| TI firmware wedged | Press RESET on the TI radar, then relaunch |
| Server preparation or timeout | Review the displayed terminal-log path |

| OPS unavailable | TI firmware wedged |
|---|---|
| ![OPS radar startup failure](assets/startup-splash-ops-error.png) | ![TI radar reset guidance](assets/startup-splash-ti-error.png) |

Select **Return to desktop** after a failure. This dismisses the error state and
releases the launch lock before the next attempt.

## Troubleshooting The Desktop Entry

### A Terminal Still Opens

Refresh the generated desktop entry:

```bash
cd ~/openflight
scripts/setup/install_desktop_launcher.sh
grep -E '^(Exec|Terminal|StartupNotify)=' ~/Desktop/OpenFlight.desktop
```

The entry should report `Terminal=false`, `StartupNotify=false`, and an `Exec`
line that calls `~/run-openflight.sh` through Bash without `lxterminal`.

### The Desktop Still Asks How To Open The File

Run the installer from the logged-in Pi desktop session so `gio` can set the
desktop trust metadata. The file must also remain executable:

```bash
chmod +x ~/run-openflight.sh ~/Desktop/OpenFlight.desktop
gio set ~/Desktop/OpenFlight.desktop metadata::trusted true
```

### Tapping Appears To Do Nothing

Another OpenFlight launcher may still hold the shared lock. Dismiss any visible
startup error with **Return to desktop**. If no UI is visible, inspect the
running processes and logs:

```bash
pgrep -af 'openflight-server|startup_splash_server'
ls -lt ~/openflight_sessions/terminal_logs | head
```

### Duplicate Desktop Icons

Keep the generated `OpenFlight.desktop`. Move old OpenFlight desktop entries to
a backup directory rather than maintaining multiple shortcuts with different
hardware arguments.

## Promotion And Rollback

The splash remains controlled by one local option:

- **Promote:** keep `--startup-splash` in `~/run-openflight.sh`.
- **Roll back:** remove only `--startup-splash`; hardware arguments and the
  terminal-free desktop entry continue to work.
- **Restore a customized launcher:** copy back the user's backup of
  `~/run-openflight.sh`, then rerun `install_desktop_launcher.sh`.

The Raspberry Pi field pass covered successful OPS/TI/power startup, unplugged
OPS and TI failures, a wedged TI firmware failure, repeated desktop taps,
failure dismissal, and relaunch. The splash remains feature-gated so a rollout
does not require changing the underlying hardware initialization sequence.

## Maintainer Verification

Run the focused contracts with:

```bash
uv run pytest tests/test_desktop_launcher.py tests/test_start_kiosk.py \
  tests/test_startup_splash_server.py tests/test_startup_status.py -v
bash -n scripts/setup/run-openflight.example.sh \
  scripts/setup/install_desktop_launcher.sh scripts/start-kiosk.sh
```
