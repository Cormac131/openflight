# Sound Trigger Wiring Guide

Step-by-step instructions for wiring the sound trigger that enables spin detection in rolling buffer mode.

> **Parts needed:** See the [Parts List](PARTS.md#sound-trigger-for-rolling-buffer-mode) for what to buy.

> **Running the OPS243 on the Pi GPIO UART instead of USB?** This trigger
> wiring is unchanged — `GATE` still drives `HOST_INT` directly. See
> [Moving the OPS243 from USB to the Pi GPIO UART](ops243-uart-migration.md)
> for the data and power side.

## Overview

The SparkFun SEN-14262 sound detector listens for club impact and triggers the OPS243-A radar to dump its I/Q buffer. That captured data is then analyzed for spin rate estimation.

The wiring is simple — three wires off the detector, plus the radar's ground
return:

```
SEN-14262 GATE → OPS243-A HOST_INT (J3 Pin 3)
SEN-14262 VCC  → Raspberry Pi 3.3V
SEN-14262 GND  → Raspberry Pi GND
OPS243-A GND   → Raspberry Pi GND (J3 Pin 10, shared rail)
```

![Sound trigger wiring: the SEN-14262 sound detector runs on 3.3V from Raspberry Pi header pin 1 with ground on pin 6, its GATE output drives HOST_INT on OPS243 J3 pin 3, and OPS243 ground on J3 pin 10 returns to the Pi.](assets/sound-trigger-wiring.svg)

*The OPS243 keeps its micro-USB connection for data and power here; only the
trigger and ground are wired by hand. To take the radar off USB as well, see
[the UART migration](ops243-uart-migration.md).*

## Before You Wire: Solder R17

The SEN-14262 is designed for 5V but runs at 3.3V in this setup. At 3.3V the preamp gain is too high and the GATE output can get stuck high. To fix this, solder a through-hole resistor into the **R17** position on the SEN-14262 board.

R17 sits in parallel with the onboard 100kΩ surface-mount R3, reducing the preamp gain:

| R17 Value | Effective Resistance | Gain Reduction |
|-----------|---------------------|----------------|
| 47kΩ | ~32kΩ | Moderate — try this first |
| 33kΩ | ~25kΩ | More aggressive — for noisy environments |

Start with 47kΩ. If the GATE LED still stays lit without sound, switch to a lower value.

> **Want to change sensitivity without a soldering iron?** Fit a DS3502 digital
> potentiometer to the R17 pads instead of a fixed resistor and tune it from the
> UI. See [Optional: Software-Controlled Sensitivity](#optional-software-controlled-sensitivity-ds3502-digital-pot)
> below. Wire the rest of the trigger first and get it working with a soldered
> resistor — the digipot replaces one component, not the whole guide.

---

## Wiring

### Step 1: Identify OPS243-A J3 Header Pins

`J3` is the **10-pin** header on the OPS243-A. Pin 1 is at the **right** end, so
the numbering runs right to left:

```
OPS243-A J3 header:
┌────┬────┬────┬────┬────┬────┬────┬────┬────┬────┐
│ 10 │  9 │  8 │  7 │  6 │  5 │  4 │  3 │  2 │  1 │
│GND │ 5V │    │TxD │RxD │    │    │INT │    │IO  │
└────┴────┴────┴────┴────┴────┴────┴────┴────┴────┘
```

The two pins this guide needs:

- **Pin 3** = HOST_INT (trigger input)
- **Pin 10** = GND

### Step 2: Connect Power

1. Connect **SEN-14262 VCC** → **Pi 3.3V** (physical pin 1)
2. Connect **SEN-14262 GND** → **Pi GND** (physical pin 6)
3. Connect **OPS243-A GND (J3 Pin 10)** → same **Pi GND** rail

All three boards must share a common ground.

### Step 3: Connect Trigger

1. Connect **SEN-14262 GATE** → **OPS243-A HOST_INT (J3 Pin 3)**

That's it. No level shifter, no MOSFETs, no breadboard needed.

```
SEN-14262               Raspberry Pi           OPS243-A
┌───────────┐          ┌──────────┐          ┌──────────┐
│ VCC ──────┼──────────┤ 3.3V     │          │          │
│           │          │          │          │          │
│ GATE ─────┼──────────┼──────────┼──────────┤ HOST_INT │
│           │          │          │          │ (J3 P3)  │
│ GND ──────┼──────────┤ GND      ├──────────┤ GND      │
│           │          │          │          │ (J3 P10) │
└───────────┘          └──────────┘          └──────────┘
```

---

## Wiring Checklist

- [ ] R17 resistor soldered on SEN-14262 board
- [ ] SEN-14262 VCC → Pi 3.3V (pin 1)
- [ ] SEN-14262 GND → Pi GND (pin 6)
- [ ] SEN-14262 GATE → OPS243-A HOST_INT (J3 Pin 3)
- [ ] OPS243-A GND (J3 Pin 10) → Pi GND (shared ground)

---

## One-Time Radar Setup

The OPS243-A must have rolling buffer mode saved to persistent memory for HOST_INT triggers to work. This is due to a firmware bug where the HOST_INT pin mode changes when transitioning modes at runtime.

```bash
# Configure and save rolling buffer mode to flash (one-time)
uv run python scripts/hardware-test/test_rolling_buffer_persist.py --setup

# Power cycle the radar (unplug USB, wait 3s, replug)

# Verify
uv run python scripts/hardware-test/test_rolling_buffer_persist.py --test
```

---

## Testing

### Quick Test: Visual

Make a loud sound near the SEN-14262. The onboard LED should flash briefly, then turn off. If the LED stays on constantly, you need a lower-value resistor in R17.

### Full Test: Software

```bash
uv run python scripts/hardware-test/test_sound_trigger_hardware.py
```

You should see:
```
Ready for hardware sound triggers!
Make a sound near the sensor... (Ctrl+C to quit)

[1] Waiting for hardware trigger (timeout=60s)...
  TRIGGER RECEIVED after 0.02s!
  I/Q samples: 4096 I, 4096 Q
```

---

## Optional: Software-Controlled Sensitivity (DS3502 Digital Pot)

A soldered R17 fixes the detector's gain at one value. Replacing it with an
**Adafruit DS3502** I2C digital potentiometer puts that resistance under
software control, so sensitivity becomes a slider on the **Debug → Sound** page
instead of a trip to the bench. Everything else about the trigger is unchanged:
`GATE` still drives `HOST_INT` directly, and the ~10 µs hardware latency is
untouched.

This is optional. Skip it if a fixed resistor works for where you hit.

### Why It Works (and When a Series Resistor Is Needed)

`R17` sits in parallel with the SEN-14262's onboard 100 kΩ `R3`, and together
they set the preamp's gain. A **lower** parallel resistance means **less gain**,
so a lower R17 makes the detector **less** sensitive.

**A 100 kΩ MCP4017 needs nothing in series.** Its span covers 0 to 100 kΩ, so
this guide's 33 kΩ and 47 kΩ settings both land comfortably inside it — at steps
42 and 60 of 127. Leave `--sound-sensitivity-series-ohms` alone.

**A 10 kΩ DS3502 cannot reach the operating point unaided.** The recommended R17
is 47 kΩ; 10 kΩ in parallel with `R3` gives a 9.1 kΩ preamp leg against 32 kΩ at
that baseline, so *every* setting would be far less sensitive than the
documented starting point — the detector would likely miss strikes outright. A
fixed resistor in series with the wiper shifts its span up to where it is
useful:

| Series resistor | R17 span | Preamp leg | Suits |
|-----------------|----------|------------|-------|
| 27 kΩ | 27 – 37 kΩ | 21.3 – 27.0 kΩ | A noisy room; most damping |
| **33 kΩ (default)** | **33 – 43 kΩ** | **24.8 – 30.1 kΩ** | **Most builds** |
| 39 kΩ | 39 – 49 kΩ | 28.1 – 32.9 kΩ | A quiet room; most gain |

33 kΩ is the default because its bottom end lands exactly on this guide's
"noisy environment" value and its top end reaches close to the recommended
47 kΩ — a 6% gain difference. Tell the server which one you fitted with
`--sound-sensitivity-series-ohms`, or every resistance the UI reports is wrong.

Resolution is **79 Ω per step** across 128 steps, concentrated entirely in the
useful region.

### Why I2C Rather Than a Three-Wire Pot

Parts like the X9C104 use a three-wire pulse protocol with no readback. That
combination is unforgiving: the wiper position exists only as a guess in
software, an electrically noisy pulse train silently miscounts, and nothing can
detect the drift. Both supported parts avoid all of it:

- **I2C**, so there is no pulse train to get wrong, and no timing to tune.
- **The wiper reads back**, so the position OpenFlight shows is measured, not
  remembered — it cannot drift out of sync.
- **They claim no GPIOs.** They share the I2C bus the inclinometer and any UPS
  fuel gauge already use.

The DS3502 additionally keeps its setting in its own EEPROM. The MCP4017's wiper
is volatile and returns to mid-scale on every power-up, so OpenFlight saves the
setting to `~/.config/openflight/sound_sensitivity.json` and re-applies it at
startup instead — the difference is invisible in use.

### Parts

- One **Adafruit DS3502** breakout ([product 4286](https://www.adafruit.com/product/4286)).
- One **series resistor** — 33 kΩ unless the table above says otherwise.
- Either five Dupont jumpers, **or** one JST-SH STEMMA QT cable plus a single
  jumper for `V+` — see Step 2.
- Two short leads to the R17 pads.

### Step 1: Cut the V+ Jumper

Do this before anything else. **`V+` is the wiper bias pin**, and Adafruit ship
the board with a solder jumper tying it to `RH`. Their pinout page: *"By default
this is connected with a jumper to RH but you can cut the solder jumper and wire
it directly."*

This build uses the pot as a two-terminal variable resistor — `RW` and `RL`
only, with `RH` unconnected — so with the jumper intact `V+` would float and the
wiper's MOSFET gates would be unbiased. Leaving the jumper *and* driving `RH`
instead is not an option either: `RH` connects through the resistor element to
`RL`, which is your preamp node, so any voltage there injects DC into the audio
path.

**Cut the `V+`/`RH` jumper, then supply `V+` separately** as below.

### Step 2: Connect the Pot to the Pi

Two ways, depending on whether you already have the LIS3DH inclinometer fitted.

#### Option A — Dupont wires

Five wires straight to the Pi header:

| DS3502 | Pi header | Note |
|--------|-----------|------|
| `VCC` | Physical **1** or **17** (3.3 V) | Logic supply |
| `GND` | Physical **9** (GND) | Shared rail |
| `SDA` | Physical **3** (BCM2) | Shared I2C bus |
| `SCL` | Physical **5** (BCM3) | Shared I2C bus |
| `V+` | Physical **2** or **4** (5 V) | Wiper bias — see Step 1 |

#### Option B — STEMMA QT chained from the LIS3DH

If you have the [LIS3DH inclinometer](inclinometer/README.md), it is an Adafruit
STEMMA QT board with a spare port. One JST-SH cable from it to the DS3502 covers
`VCC`, `GND`, `SDA` and `SCL` in a single click — no header pins to find and
nothing to mis-wire.

```
   Pi header ──[existing wiring]── LIS3DH ──[JST-SH cable]── DS3502
                                                                │
                        Pi 5V (physical 2 or 4) ────────────────┘  V+
```

| Connection | Carries |
|------------|---------|
| JST-SH cable, LIS3DH spare port → DS3502 QT port | `VCC` (3.3 V), `GND`, `SDA`, `SCL` |
| One Dupont wire, Pi physical **2** or **4** → DS3502 `V+` | Wiper bias |

**The QT cable does not carry `V+`** — it is a 4-conductor I2C cable, and `V+`
is specific to this board. So Option B is one cable plus one wire, not one cable
alone. That single wire is the whole difference between the two options.

Either way the pot lands on the same bus the inclinometer (0x18) and any
Geekworm UPS fuel gauge (0x36) already use. The DS3502 sits at **0x28** and does
not clash; the `A0`/`A1` jumpers give 0x28–0x2b if you need to move it, passed
with `--sound-sensitivity-address`.

Confirm the Pi can see it before going further:

```bash
i2cdetect -y 1     # expect 28 in the grid, alongside 18 and 36 if fitted
```

### Step 3: Connect the Pot to the R17 Pads

The wiper and the low terminal go across the R17 footprint, with the series
resistor in the wiper leg:

```
   DS3502                                     SEN-14262
┌───────────┐                               ┌─────────────┐
│  RW ──────┼───[ 33kΩ series ]─────────────┤ R17 pad     │
│           │                               │  (parallel  │
│  RL ──────┼───────────────────────────────┤   with R3)  │
│           │                               │             │
│ GND ──────┼──────── Pi GND ───────────────┤ GND         │
└───────────┘                               └─────────────┘
```

- **`RH` is left unconnected.** Only `RW` and `RL` are used, so the pot acts as
  a variable resistor rather than a divider.
- **R17 is not polarised** — it is a plain two-pad footprint, so either lead can
  go to either pad.
- Keep the two leads short. This is a high-impedance node on an audio preamp;
  long unshielded wire picks up hum that reads as a false trigger.
- All boards must still share a ground with the SEN-14262 and the OPS243-A.

### Step 4: Enable It

```bash
scripts/start-kiosk.sh --sound-sensitivity
```

On startup the server prints the step it found:

```
Sound sensitivity control enabled (mcp401x step 64, ~50494 ohm R17)
```

On a DS3502, add `--sound-sensitivity-device ds3502` and
`--sound-sensitivity-series-ohms` if you fitted something other than 33 kΩ.

If the pot cannot be reached, the server logs a warning and carries on — the
detector keeps whatever gain the hardware is at, and the Debug page shows the
control as unavailable with the reason. Sensitivity is a convenience, never a
prerequisite for detecting a shot.

| Flag | Purpose |
|------|---------|
| `--sound-sensitivity` | Enable the control (off by default) |
| `--sound-sensitivity-device` | `mcp401x` (default) or `ds3502` |
| `--sound-sensitivity-series-ohms N` | The series resistor you fitted. Defaults per device: none for the MCP4017, 33000 for the DS3502 |
| `--sound-sensitivity-address N` | I2C address. Defaults per device; the MCP4017's 0x2f is fixed |
| `--sound-sensitivity-i2c-bus N` | I2C bus (default 1) |
| `--sound-sensitivity-position N` | Force step `N` (0–127) for this run, without persisting it |

### Step 5: Verify with a Meter

Sweep the wiper and watch the resistance across the R17 pads:

```bash
uv run python scripts/hardware-test/test_digipot.py --sweep
```

**Power the SEN-14262 down first**, or better, lift one lead off the pads. R17
is in the feedback path around a live op-amp, and a meter on a powered node
reads the amplifier's response to room noise rather than a resistance.

Expect the reading to climb smoothly from the series resistor's value to that
plus 10 kΩ — 33 kΩ to 43 kΩ with the default. In circuit (unpowered) you will
instead read that in parallel with the board's 100 kΩ `R3`, so 24.8 kΩ to
30.1 kΩ. Either way what matters is that it **moves monotonically** with the
printed step.

### Step 6: Find the Ceiling for Your Room

A meter proves the resistance moved. It cannot tell you whether *sensitivity*
moved. For that, sweep while counting `GATE` edges on the trigger line:

```bash
uv run python scripts/hardware-test/test_digipot.py --noise-floor
```

Run it in the room you hit in, as quiet as it gets during play, and do not hit
anything. Each step gets a dwell window:

```
  step        R17   edges    high  verdict
  ----  ---------  ------  ------  ---------
     0     33.00k       0      0%  quiet
    32     35.52k       0      0%  quiet
    64     38.04k       3      2%  active
  ...

Ambient noise started firing the trigger at step 64; backing off 6 steps lands on 58.
```

The step where `active` first appears is where room noise alone fires the
trigger — the **ceiling**. Two other verdicts matter:

- **`active`** — the GATE is pulsing; ambient noise is getting through.
- **`saturated`** — the GATE is high essentially the whole window. That is the
  classic over-gain failure, and it is invisible to an edge count alone: a
  latched-high line produces *no* transitions, so counting edges would rank the
  worst case as the quietest. If step 0 is already saturated, gain is not the
  problem — go back to the wiring checks.

### Step 7: Tune from the UI

Open **Debug → Sound** and use the **Sound Trigger Sensitivity** slider. The
readout shows the applied percentage, the resistance R17 now presents, and the
resulting preamp leg.

- **Triggering on ambient noise** (a door, a fan, a neighbouring bay) — turn it
  **down**.
- **Missing strikes**, or the GATE LED not flashing on a clean hit — turn it
  **up**.
- **GATE LED stuck on** at every setting — gain is not the problem; go back to
  the wiring checks below.

To find the **floor**, start at the ceiling Step 6 gave you and walk down while
hitting shots until strikes stop registering. Your working setting is between
the two. Watch **Debug → Status** while you do it: the trigger counters and Last
Trigger card show whether each strike was accepted, faster than waiting for a
shot to appear.

If the two ends cross — ambient noise fires the trigger *below* where strikes
reliably register — no setting will work, and gain is not the lever. Move the
detector away from the noise source, or damp it.

### Optional: Closed-Loop Auto Gain (ADS1115)

Everything above tunes the gain by hand. The detector's **`ENVELOPE`** output —
the analogue amplitude the preamp actually saw, as opposed to `GATE`'s bare
"loud enough" — lets the software do it instead. The Pi has no analogue input,
so this needs a small ADC.

```
  SEN-14262 ──GATE──────────────────────────► OPS243-A HOST_INT
      │
      └──────ENVELOPE──► ADS1115 ──I2C──► Pi ──I2C──► DS3502 ──► R17
                                                                  │
      ▲───────────────────────────────────────────────────────────┘
```

After each shot the server reads the envelope peak from that shot and nudges
the pot **between shots, never during one**:

| Peak vs the target band | Action |
|-------------------------|--------|
| At or near the rail (clipping) | Drop gain immediately, without waiting |
| Above the band | Lower gain |
| Inside the band (60–80% by default) | Hold |
| Below the band | Raise gain |

It decides on the **median of the last few shots**, not the last one, so a
thinned strike does not move the gain on its own; it clears that history
whenever it moves, because peaks measured at the old gain say nothing about the
new one; and it steps only part of the way toward the correction, because the
envelope is only approximately linear in gain.

#### What It Can and Cannot Fix

**This is a trim, not a wide-range AGC — and with the default series resistor it
has almost no authority at all.** R17 works against the board's fixed 100 kΩ R3,
so the pot's whole travel moves the preamp leg very little:

| Series resistor | Gain range, end to end | Wider than a 60–80% band (1.33×)? |
|-----------------|------------------------|-----------------------------------|
| 5 kΩ | 2.74× | yes |
| 10 kΩ | 1.83× | yes |
| 20 kΩ | 1.38× | yes |
| 27 kΩ | 1.27× | **no** |
| **33 kΩ (default)** | **1.21×** | **no** |
| 39 kΩ | 1.17× | **no** |

A 60–80% band spans a ratio of 1.33×. With 33 kΩ fitted, that band is **wider
than the entire adjustment range**, so once a peak is inside it no reachable
wiper step can push it out — the loop holds forever and looks broken when it is
simply out of travel. The server logs a warning at startup when that is the
case.

Two ways to give it authority, and you want one of them:

- **Narrow the band**, e.g. `--sound-sensitivity-target-low 0.68
  --sound-sensitivity-target-high 0.76` (1.12×, comfortably inside 1.21×).
- **Fit a smaller series resistor**, which widens the relative range at the cost
  of lower absolute gain.

Either way the series resistor still sets the *window*; auto gain only trims
within it. If your detector is badly placed, no setting in the loop will save
it, and the controller will say so rather than sitting at an end stop.

#### Parts and Wiring

One **ADS1115** breakout — Adafruit's has STEMMA QT, so it chains off the same
cable as the DS3502.

| ADS1115 | Connect to |
|---------|------------|
| `VDD` | Pi 3.3 V (or the QT chain) |
| `GND` | Pi GND |
| `SDA` / `SCL` | Pi physical 3 / 5 (or the QT chain) |
| `A0` | SEN-14262 **`ENVELOPE`** |

The ADC sits at **0x48** by default (`ADDR` selects 0x48–0x4b), clear of the
DS3502 at 0x28, the inclinometer at 0x18 and any UPS gauge at 0x36. Unlike the
DS3502 it has no `V+` to worry about.

```bash
i2cdetect -y 1                                   # expect 48 as well as 28
scripts/start-kiosk.sh --sound-sensitivity --sound-sensitivity-auto
```

| Flag | Purpose |
|------|---------|
| `--sound-sensitivity-auto` | Enable the loop (requires `--sound-sensitivity`) |
| `--sound-sensitivity-target-low/-high` | Target band, as fractions (default 0.60/0.80) |
| `--sound-sensitivity-envelope-address N` | ADS1115 address, 0x48–0x4b |
| `--sound-sensitivity-envelope-channel N` | Which single-ended input carries `ENVELOPE` (default 0) |
| `--sound-sensitivity-detector-volts N` | The detector's own supply, which is where it clips (default 3.3) |

#### Using It

**Debug → Sound** gains an **Auto gain** toggle, the last envelope peak as a
percentage, and a line saying what the loop just decided and why. Moving the
slider by hand switches the loop off — a manual value and a running loop would
fight, and the loop would win.

Auto adjustments are **volatile**. The settled value is committed to the pot's
EEPROM once it has held for ten shots, so a session starts near the right place
without spending a write per shot on a part with finite endurance.

### Where the Setting Lives

**It depends on the part, and OpenFlight handles both.**

- **MCP4017** — the wiper is RAM only and powers up at mid-scale, so the setting
  is saved to `~/.config/openflight/sound_sensitivity.json` and re-applied when
  the server starts.
- **DS3502** — every change is committed to the chip's own EEPROM, so it
  survives a power cycle with nothing on the Pi. OpenFlight reads the wiper back
  at boot and reports whatever it finds.

`--sound-sensitivity-position` is the exception on either part: it steers a
single run without persisting, so it never overwrites what you deliberately
set.

### Digipot Wiring Checklist

- [ ] MCP4017 (**not** MCP4018/4019 — their `B` terminal is grounded internally)
- [ ] `V+`/`RH` solder jumper **cut** — DS3502 only
- [ ] Power and I2C by **one** of:
      - Dupont: `VCC` → Pi 3.3 V (pin 1/17), `GND` → Pi GND (pin 9),
        `SDA` → pin 3, `SCL` → pin 5
      - STEMMA QT: JST-SH cable from the LIS3DH's spare port
- [ ] DS3502 `V+` → Pi 5 V (physical pin 2 or 4) — a separate wire either way, DS3502 only
- [ ] `i2cdetect -y 1` shows the pot at 0x28
- [ ] MCP4017 `W` and `B` → the two R17 pads (nothing in series), **or**
      DS3502 `RW` → series resistor → one pad and `RL` → the other, `RH` unconnected
- [ ] No fixed resistor left soldered in R17 (it would parallel the pot)
- [ ] `--sound-sensitivity` passed to the server, with `--sound-sensitivity-device ds3502` and `--sound-sensitivity-series-ohms` if not on the default MCP4017

For the optional closed loop:

- [ ] ADS1115 powered and on the bus (`i2cdetect -y 1` shows `48`)
- [ ] SEN-14262 `ENVELOPE` → ADS1115 `A0`
- [ ] `--sound-sensitivity-auto` passed
- [ ] Target band narrower than the gain range your series resistor allows

---

## Troubleshooting

### GATE LED stays on (stuck high)

The preamp gain is too high for 3.3V operation.
- **Fix:** Solder a lower-value resistor into R17 (try 33kΩ instead of 47kΩ)

### No trigger received

1. Check the GATE LED flashes when you clap
2. Verify GND is shared between all three boards (Pi, SEN-14262, OPS243-A)
3. Verify HOST_INT is J3 **Pin 3** (not Pin 2)
4. Run `uv run python scripts/hardware-test/test_rolling_buffer_persist.py --test` to confirm radar is in rolling buffer mode

### Triggers constantly / too sensitive

- Use a lower-value R17 resistor to reduce gain
- Move the sensor further from the hitting area

### Triggers but no I/Q data

- Run the one-time radar setup (see above) — HOST_INT mode must be saved to flash
- Power cycle the radar after setup

### Digital pot: "Sound sensitivity control unavailable" at startup

The server could not claim the GPIO lines.

1. Check nothing else already holds them — a second server, or a hardware-test
   script left running.
2. Confirm the user is in the `gpio` group: `groups | grep gpio`.
3. If this Pi exposes the header on a different gpiochip, set
   `OPENFLIGHT_GPIO_CHIP`.

### Digital pot: not detected at startup

The server could not reach the DS3502.

1. `i2cdetect -y 1` — the pot should appear at `2f` (MCP4017) or `28`
   (DS3502). Nothing there means wiring, power, or the wrong bus. On a STEMMA QT
   chain, check the cable is in the LIS3DH's *spare* port and clicked in at both
   ends.
2. **DS3502 only:** check `V+` has 4.5 V or more, and that its jumper to `RH` is
   cut. Without bias the chip answers on I2C while the wiper does nothing —
   which looks like a dead pot but is a two-minute fix.
3. Check `--sound-sensitivity-device` matches the part you fitted; the two use
   different addresses, so the wrong one finds nothing.
3. Confirm I2C is enabled (`sudo raspi-config`) and the user is in the `i2c`
   group.
4. If the `A0`/`A1` jumpers are set, pass the matching
   `--sound-sensitivity-address`.

### Digital pot: slider moves but the detector does not change

The chip is answering on I2C (the server would have errored otherwise), so the
problem is on the resistor side.

1. Power the detector down, then sweep with a meter across the R17 pads:
   `uv run python scripts/hardware-test/test_digipot.py --sweep`. A reading stuck
   at one value means a cold joint on `RW`, `RL`, the series resistor, or a pad.
2. Check no fixed resistor is still soldered into R17 — it parallels the pot and
   flattens its range.
3. Confirm `--sound-sensitivity-series-ohms` matches what you actually fitted.
   A mismatch does not change behaviour, but every number the UI shows will be
   wrong, which makes a working pot look broken.

### Auto gain never moves — it always says "Holding"

Almost certainly the target band is wider than the gain range your series
resistor allows, so every peak is inside it by construction. The server logs a
warning at startup saying exactly this. Narrow the band or fit a smaller series
resistor; see
[What It Can and Cannot Fix](#what-it-can-and-cannot-fix).

### Auto gain says "Out of travel"

The loop wants a gain it cannot reach. That is the controller working
correctly — the series resistor has put the window in the wrong place, or the
detector is too far from the ball. Change the series resistor (larger for more
gain, smaller for less) rather than looking for a software setting.

### Digital pot: the range feels wrong at both ends

The series resistor decides where the 10 kΩ span sits. If the detector is too
sensitive even at step 0, fit a smaller one (27 kΩ); if it is not sensitive
enough at step 127, fit a larger one (39 kΩ). See the table in
[Why It Works](#why-it-works-and-when-a-series-resistor-is-needed).

### Digital pot: meter readings do not track the step

Readings that jump around or fall as the step rises are an invalid measurement,
not a miswired pot. The usual cause is measuring with the **SEN-14262 still
powered**: R17 is in the feedback path around a live op-amp, which drives that
node itself, so an ohmmeter reads the amplifier's response to room noise.

Power the detector down completely, or lift one lead off the pads, and probe the
pot's own terminals.

