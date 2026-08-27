# The OpenFlight SD card image

A prebuilt SD card image so that setting up a launch monitor is: write the
card, put it in, switch it on. No terminal, no `git clone`, no flags to
remember. Everything that can be worked out from the hardware is worked out
by the machine; the two or three things that cannot are in one plain-text
file on the card.

- [For owners](#for-owners) — writing and using a card
- [How hardware detection works](#how-hardware-detection-works)
- [What the image contains](#what-the-image-contains)
- [Building an image](#building-an-image)
- [Troubleshooting](#troubleshooting)

---

## For owners

### 1. Write the card

Download `openflight-<date>-<revision>.img.xz` from the
[Releases page](https://github.com/jewbetcha/openflight/releases) and write it
with [Raspberry Pi Imager](https://www.raspberrypi.com/software/):

1. **Choose OS** → **Use custom** → pick the `.img.xz` file.
2. **Choose Storage** → your SD card (16 GB or larger).
3. Click the **gear icon** and set:
   - your **Wi-Fi network and password**
   - a **username and password** for the machine
   - **Enable SSH** if you ever want to log in remotely
4. **Write**.

Set the Wi-Fi and account in Imager, not in a file on the card — Imager stores
the password safely, and a plain-text file on the boot partition does not.

### 2. First boot

Put the card in, connect the radar, and switch on. The first boot:

1. Shows the OpenFlight splash screen.
2. Works out which radars and sensors you have connected.
3. Writes the correct start-up mode into the OPS243-A radar's memory.
4. **Switches the unit off.** This is expected — see below.

Switch it back on. From then on it boots straight to the launch monitor.

> **Why it switches off once.** The OPS243-A has a firmware bug: the mode that
> makes the sound trigger work only takes effect after the *radar* loses
> power, and a reboot does not do that. Powering the whole unit off and on
> does. It happens exactly once, on the first boot.

### 3. Settings (optional)

Put the card back in your computer. A drive called **bootfs** appears; open
`openflight.conf` on it in any text editor.

You do not need to change anything to get started. The file covers the few
things the launch monitor cannot measure for itself:

| Setting             | What it is                                       |
| ------------------- | ------------------------------------------------ |
| `SESSION_LOCATION`  | A name for this spot, recorded in session logs    |
| `NET_DISTANCE`      | Distance to your net or screen, in metres         |
| `IWR6843_TEE_M`     | Radar-to-ball distance if your build is not standard |
| `IWR6843_NET_M`     | Radar-to-net distance if your build is not standard |
| `KLD7_MOUNT_TILT`   | Radar face tilt — **required** for older K-LD7 builds |
| `OPENFLIGHT_ENABLE_SIM` | Send shots to GSPro, E6 Connect, and similar  |

Remove the `#` from the start of a line to turn a setting on. Eject the card,
put it back in the launch monitor, and switch on.

---

## How hardware detection works

One image has to run correctly on a bare OPS243-only build, on an
OPS243 + IWR6843 build, and on a legacy K-LD7 build. Rather than shipping
three images or asking the owner which they have, the machine looks.

`start-kiosk.sh --auto-hardware` runs
[`openflight.provisioning`](../src/openflight/provisioning/) before the
server starts, on **every** boot — so moving a radar to a different USB port,
or adding one later, needs no reconfiguration.

| Hardware                | How it is recognised                          | Effect                              |
| ----------------------- | --------------------------------------------- | ----------------------------------- |
| OPS243-A (USB)          | CDC-ACM device (`/dev/ttyACM*`)                | `--radar-port <port>`               |
| OPS243-A (GPIO UART)    | `/dev/ttyAMA0`, only when no USB radar is found | `--radar-port /dev/ttyAMA0`        |
| IWR6843                 | CP2105 (`10c4:ea70`) or XDS110 (`0451:bef3`)   | `--iwr6843 --iwr6843-port <port>`   |
| K-LD7 (deprecated)      | `/dev/kld7_*` udev names, else FTDI/CP2102     | `--kld7 …`, only if tilt is known   |
| LIS3DH inclinometer     | I2C `WHO_AM_I` = `0x33` at `0x18`/`0x19`       | `--inclinometer`                    |
| Geekworm UPS            | MAX17043 at `0x36` reading a plausible voltage | `--battery geekworm`                |
| CSI camera              | `rpicam-hello --list-cameras`                  | reported only, never enabled        |

Three of those rows are decisions rather than lookups, and each one exists
because the obvious behaviour would be worse:

- **A K-LD7 with no `KLD7_MOUNT_TILT` stays off.** A guessed tilt does not
  fail loudly; it quietly skews every launch angle. Running without angle
  data beats running with wrong angle data.
- **A K-LD7 alongside an IWR6843 stays off.** The IWR6843 supersedes it, and
  running both means two answers to the same question.
- **A camera is never auto-enabled.** The camera estimators are not on the
  production radar path and their dependencies are an optional extra, so
  enabling one would turn a working image into a failing one the moment
  somebody plugs a camera in.

Detected flags are *prepended* to whatever else is on the command line, so
anything typed by hand still wins:

```bash
# See what would be detected, without starting anything
uv run python -m openflight.provisioning --report

# The flags themselves
uv run python -m openflight.provisioning --flags-line

# Full detail, for a bug report
uv run python -m openflight.provisioning --json
```

The result of the last detection run is also saved to
`/etc/openflight/hardware.env`, which is the first file to read when
answering a support question.

### Settings the machine cannot detect

Bay geometry is physical, not electrical: nothing on the bus knows how far
the net is. Those values come from `openflight.conf` on the boot partition,
which `start-kiosk.sh` parses — not sources — into the environment. Only
`NAME=VALUE` lines are honoured, so a typo cannot turn into a command.

Values already set in the environment win over the file, which keeps
`KLD7_MOUNT_TILT=9 ./scripts/start-kiosk.sh` working for a one-off test.

---

## What the image contains

Built on the current **stable Raspberry Pi OS Desktop (64-bit)** release,
resolved at build time from Raspberry Pi's own `raspios_arm64_latest`
redirect and verified against their published checksum. Building on the
released image rather than assembling one with `pi-gen` means the base is
exactly the OS Raspberry Pi ship and test, and a build takes minutes rather
than hours.

On top of that:

| Path                                       | What it is                              |
| ------------------------------------------ | --------------------------------------- |
| `/opt/openflight`                          | The project, at one committed revision   |
| `/boot/firmware/openflight.conf`           | The owner-editable settings file         |
| `/etc/openflight/hardware.env`             | The last detection result                |
| `/var/lib/openflight/hardware-report.txt`  | The readable version of the same         |
| `/var/log/openflight-firstboot.log`        | What first boot did                      |
| `/etc/xdg/autostart/openflight-kiosk.desktop` | Starts the kiosk at login             |
| `/etc/systemd/system/openflight-firstboot.service` | One-shot provisioning           |
| `/usr/share/plymouth/themes/openflight/`   | The boot splash                          |

Also configured: I2C and the GPIO UART enabled, the serial console
disabled (its boot chatter is parsed as radar API commands — `A` followed by
`!` is a flash write), screen blanking off, and Chromium's crash-restore
prompt suppressed, since there is no keyboard to dismiss it with.

The kiosk starts from `/etc/xdg/autostart` rather than a systemd service
because the image cannot know the username Imager will create; first boot
hands `/opt/openflight` to whichever account exists at UID 1000 and adds it
to `dialout`, `i2c`, `gpio`, and `video`.

### Branding

The splash screen, wallpaper, and icons are generated at build time from
`ui/public/openflighttransparentlogo.png` and the UI's own palette
(`#0e0f10` background, `#ffd400` accent) by
[`make_branding.py`](../scripts/image/make_branding.py). Deriving them rather
than committing a dozen PNGs means a logo change is a one-file change, and
the boot screen can never drift from the app. Matching backgrounds also make
firmware → splash → desktop → app one continuous screen instead of three
flashes of different colours.

Preview them without building an image:

```bash
uv run --extra image python scripts/image/make_branding.py --out /tmp/branding
```

---

## Building an image

```bash
sudo apt install qemu-user-static binfmt-support xz-utils parted \
                 e2fsprogs kpartx curl
sudo ./scripts/image/build-image.sh
```

Roughly 20 minutes and ~16 GB of free disk. Useful options:

```bash
sudo ./scripts/image/build-image.sh --no-compress          # skip the slow xz pass
sudo ./scripts/image/build-image.sh --base ~/raspios.img.xz  # pin the base release
sudo ./scripts/image/build-image.sh --out /tmp/openflight.img
```

The build downloads and verifies the base image, grows it, loop-mounts it,
runs [`customize.sh`](../scripts/image/customize.sh) inside it under
`qemu-aarch64-static`, then shrinks the filesystem back to its used size and
compresses the result. The image is staged from `git archive HEAD`, so it
contains one committed revision and none of the build host's local state; the
revision is recorded at `/opt/openflight/.image-revision`.

The three scripts are separate because they change for different reasons:

| Script             | Runs on          | Responsibility                          |
| ------------------ | ---------------- | --------------------------------------- |
| `build-image.sh`   | the build host   | mount, pack, shrink — "how"             |
| `customize.sh`     | inside the chroot | packages, config, branding — "what"     |
| `firstboot.sh`     | the owner's Pi   | anything needing the real hardware      |

### In CI

`.github/workflows/sd-image.yml` runs the same script on a hosted runner. It
is deliberately not on the ordinary push path — a build is ~20 minutes and a
~1.5 GB artifact. Trigger it from the Actions tab, or push a `v*` tag, which
also attaches the image to the release.

---

## Troubleshooting

**It switched off during the first boot.** That is the expected one-time
radar power cycle. Switch it back on.

**No ball speed / "No OPS243-A radar found".** Check the radar's USB cable
and reboot. `/var/lib/openflight/hardware-report.txt` lists everything that
was and was not found.

**Launch angle is missing on a K-LD7 build.** Those radars stay off until
`KLD7_MOUNT_TILT` is set in `openflight.conf`. Measure the tilt of the
radar's face with a phone spirit-level app.

**Shots are not triggering.** The sound trigger is a wire, not a device, so
detection cannot check it. Run the self-test:

```bash
cd /opt/openflight && uv run python scripts/hardware-test/diagnose.py
```

**Re-run the setup.** From your computer, create an empty file named
`openflight-reprovision` on the card's **bootfs** drive and boot the unit.
With a terminal:

```bash
sudo rm /var/lib/openflight/firstboot.state
sudo systemctl start openflight-firstboot
```

**See what it decided at the last start.**

```bash
cat /etc/openflight/hardware.env
cat /var/log/openflight-firstboot.log
```
