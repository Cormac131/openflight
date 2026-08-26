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

> **Want to change sensitivity without a soldering iron?** Fit an X9C104 digital
> potentiometer to the R17 pads instead of a fixed resistor and tune it from the
> UI. See [Optional: Software-Controlled Sensitivity](#optional-software-controlled-sensitivity-x9c104-digital-pot)
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

## Optional: Software-Controlled Sensitivity (X9C104 Digital Pot)

A soldered R17 fixes the detector's gain at one value. Swapping it for an
**X9C104** 100 kΩ digital potentiometer puts that resistance under software
control, so sensitivity becomes a slider on the **Debug → Sound** page instead
of a trip to the bench. Everything else about the trigger is unchanged: `GATE`
still drives `HOST_INT` directly, and the ~10 µs hardware latency is untouched.

This is optional. Skip it if a fixed resistor works for where you hit.

### Why It Works

`R17` sits in parallel with the SEN-14262's onboard 100 kΩ `R3`, and together
they set the preamp's gain. A **lower** parallel resistance means **less gain**,
so a lower R17 makes the detector **less** sensitive:

| Wiper position | R17 (pot) | Preamp leg (R17 ∥ R3) | Behaviour |
|----------------|-----------|-----------------------|-----------|
| 0 | ~40 Ω | ~40 Ω | Least sensitive — near-total shunt |
| 33 | ~33 kΩ | ~25 kΩ | The guide's "noisy environment" value |
| 46 (default) | ~46.5 kΩ | ~32 kΩ | The guide's recommended 47 kΩ starting point |
| 99 | ~100 kΩ | ~50 kΩ | Most sensitive within the pot's range |

The X9C104 has 100 tap points across a 100 kΩ element, so one tap is about 1 kΩ.
OpenFlight starts it at position **46**, which lands within a step of the 47 kΩ
resistor this guide has always recommended — so fitting the pot does not change
how an existing build behaves on the first boot.

### Parts

- One **X9C104P** (8-pin DIP, 100 kΩ). Not an X9C102/103/503 — those top out at
  1 kΩ / 10 kΩ / 50 kΩ and cannot reach 47 kΩ.
- Five jumper wires to the Pi header, plus two short leads to the R17 pads.

### Where It Sits on the Pi Header

Everything OpenFlight can drive on the 40-pin header, so you can see what is
free before picking pins:

| BCM | Physical | Claimed by | When |
|-----|----------|------------|------|
| 2 / 3 | 3 / 5 | I2C — LIS3DH inclinometer, Geekworm fuel gauge | `--inclinometer`, `--battery geekworm` |
| 6 | 31 | Geekworm AC detect | `--battery geekworm` |
| 14 / 15 | 8 / 10 | UART `TXD0` / `RXD0` → OPS243 `J3` | [OPS243 on the GPIO UART](ops243-uart-migration.md) |
| 16 | 36 | Geekworm charge control (OpenFlight never reads it) | `--battery geekworm` |
| 17 | 11 | Shared sound-trigger edge → IWR6843 and camera capture | Always |
| **22 / 23 / 24** | **15 / 16 / 18** | **X9C104 `CS` / `INC` / `U/D`** | **`--sound-sensitivity`** |

Power and ground pins already spoken for on a full build:

| Pi pin | Used by |
|--------|---------|
| 1 (3.3V) | SEN-14262 `VCC` |
| 4 (5V) | OPS243 `J3` pin 9, on the GPIO UART |
| 6 (GND) | SEN-14262 `GND` |
| 14 (GND) | OPS243 `J3` pin 10, on the GPIO UART |

That leaves **pin 2** for the digipot's 5V and **pin 20** for its ground. The Pi
has eight ground pins (6, 9, 14, 20, 25, 30, 34, 39) and two 5V pins (2, 4), so
there is room either way — the point is not to double-book one hole.

The server refuses to start if the digipot pins collide with something the same
run actually drives, and the error names the conflict. The three lines are
remappable with `--sound-sensitivity-cs-pin`, `--sound-sensitivity-inc-pin` and
`--sound-sensitivity-ud-pin`.

Neither display touches the header: the HMTECH 7" is HDMI plus USB, and the
Raspberry Pi Touch Display 2 is MIPI DSI. A Geekworm X1202/X1206 reaches the
header through pogo pins from underneath, so the top of the header stays free.

### X9C104 Pinout

```
        X9C104 (8-pin DIP, notch at top)
            ┌───────∪───────┐
    INC  1 ─┤               ├─ 8  VCC
    U/D  2 ─┤               ├─ 7  CS
  RH/VH  3 ─┤               ├─ 6  RL/VL
    VSS  4 ─┤               ├─ 5  RW/VW
            └───────────────┘
```

- `CS` (7) — chip select, active low
- `INC` (1) — increment; the wiper moves on each falling edge
- `U/D` (2) — direction: high steps toward `RH`, low toward `RL`
- `RH` / `RW` / `RL` (3/5/6) — the resistor element and its wiper

### Step 1: Connect the Pot to the Pi

| X9C104 | Signal | Pi header | BCM |
|--------|--------|-----------|-----|
| Pin 8 | VCC | Physical **2** (5V) | — |
| Pin 4 | VSS | Physical **20** (GND) | — |
| Pin 7 | CS | Physical **15** | **BCM22** |
| Pin 1 | INC | Physical **16** | **BCM23** |
| Pin 2 | U/D | Physical **18** | **BCM24** |

Physical 15/16/18/20 are four pins in a row, so the whole digipot bundle lands
in one block. Ground is **pin 20**, not pin 14: on a build with the OPS243 on
the GPIO UART, [that migration's diagram](ops243-uart-migration.md) already has
pin 14 carrying the radar's ground return.

### Required: A Decoupling Capacitor

Fit a **100 nF (0.1 µF) ceramic capacitor directly across pin 8 (`VCC`) and
pin 4 (`VSS`)**, with the shortest leads you can manage — at the chip, not back
at the Pi header.

This is not optional and not a debugging step. The X9C104 is a CMOS part, and
without local decoupling its supply dips and rings on every internal switching
event. On the long jumper leads this build uses, that is enough for the wiper's
counter to miscount, so one commanded pulse advances the wiper several taps.

The signature is a **consistent overshoot that does not respond to anything you
do to the signal line**: unchanged by the supply rail, and barely changed by a
series resistor on `INC`. Both of those leave supply integrity untouched, which
is what points at decoupling.

Keep the `GND` lead short and direct for the same reason — ground bounce on a
long return has the same effect.

### Strongly Recommended: Series Resistors on the Control Lines

Put **100–330 Ω in series on `INC`**, in the wire at the Pi end, and ideally on
`CS` and `U/D` too.

Without it, long jumper leads ring. The Pi's edge is a few nanoseconds into an
inductive wire, and the resulting damped oscillation crosses the X9C104's input
threshold several times. The chip has no input filtering, so it counts every
crossing as a step: one commanded pulse becomes three or four, and the wiper
lands far past where it was told to go.

The tell is a **consistent** multiplier. Commanding tap 10 and landing near 32
every time, reproducibly, is ringing — the wiring's L/C fixes how many crossings
each edge produces, so it repeats. Measure it with `--position 10 --hold`: the
reading should be ~10 kΩ, and `(ohms / 1037) / 10` is how many taps each pulse
is actually moving.

`INC` matters most, since only its edges count as steps, but a glitch on `CS`
mid-train misframes the whole instruction. Also keep `INC` from running bundled
against `CS` and `U/D`, and keep all three leads short. If a series resistor
alone is not enough, add 100 pF from `INC` to GND at the chip end.

Note that **no software setting fixes this**. `--step-delay-us` changes the gap
between pulses, not the edge rate, and the ringing is over long before the next
pulse begins.

### Strongly Recommended: A Pull-Up on CS

Add a **10 kΩ resistor from `CS` (pin 7) to the Pi's 3.3 V** (header pin 1 or
17).

Without it, the wiper only stays where you put it for as long as some process
is driving the GPIOs. BCM 9–27 default to a pull-down, so the moment the lines
are released — a bench script exiting, the server stopping — `CS` is pulled
**low**, which *selects* the chip, and `INC` falls with it. That falling edge is
a step command, and a selected chip keeps taking steps from any noise on the
line. The wiper walks away from its setting.

The pull-up holds `CS` high whenever the Pi is not actively driving it, so an
unselected chip ignores everything and the wiper freezes.

**Pull up to 3.3 V, not to the pot's 5 V rail.** A pull-up to 5 V would put
~4.2 V on that node whenever the Pi released the line, and Pi GPIOs are **not**
5 V tolerant — that damages the Pi. 3.3 V is still comfortably above the
X9C104's 2.0 V logic-high threshold, so the chip reads it as deselected.

```
   Pi 3.3V (header pin 1 or 17)
        │
       10kΩ
        │
        ├────────── X9C104 pin 7 (CS)
        │
   Pi BCM22 (header pin 15)
```

**Power it from 5V, not 3.3V.** The X9C104's DC characteristics are specified at
5V. Its logic inputs read high from 2.0V, so the Pi's 3.3V GPIOs drive `CS`,
`INC` and `U/D` directly with no level shifter. The resistor element only ever
sees the detector's 3.3V rail, which is well inside the pot's 0–5V analog range.

See [Where It Sits on the Pi Header](#where-it-sits-on-the-pi-header) above if
you need to move these.

### Step 2: Connect the Pot to the R17 Pads

Tie **pin 3 (RH) to pin 5 (RW)** with a short link, then wire that pair to one
R17 pad and **pin 6 (RL)** to the other:

```
   X9C104                          SEN-14262
┌───────────┐                    ┌─────────────┐
│  RH (3) ──┼──┐                 │             │
│           │  ├──────────────── ┤ R17 pad     │
│  RW (5) ──┼──┘                 │             │
│           │                    │  (parallel  │
│  RL (6) ──┼─────────────────── ┤   with R3)  │
│           │                    │             │
│ VSS (4) ──┼─ Pi GND (pin 20) ──┤ GND         │
└───────────┘                    └─────────────┘
```

- **R17 is not polarised** — it is a plain two-pad footprint, so either pad can
  take either lead. What matters is that the pot's `RL` end goes to one pad and
  the wiper (`RW`) to the other. Wiring `RH` to a pad *instead of* `RL` inverts
  the slider: turning it up would make the detector less sensitive.
- **Tying `RH` to `RW` is deliberate.** It shorts out the unused half of the
  element so nothing floats, and leaves the pad-to-pad resistance equal to the
  wiper-to-`RL` value the software reports.
- Keep the two leads short. This is a high-impedance node on an audio preamp;
  long unshielded wire picks up hum that reads as a false trigger.
- All boards must still share a ground: `VSS` goes to the same Pi GND rail as
  the SEN-14262 and the OPS243-A.

### Step 3: Enable It

```bash
scripts/start-kiosk.sh --sound-sensitivity
```

On startup the server prints the tap it applied:

```
Sound sensitivity control enabled (X9C104 position 46, ~46504 ohm R17)
```

If the pot cannot be brought up, the server logs a warning and carries on — the
detector keeps whatever gain the hardware is at, and the Debug page shows the
control as unavailable with the reason. Sensitivity is a convenience, never a
prerequisite for detecting a shot.

Useful flags:

| Flag | Purpose |
|------|---------|
| `--sound-sensitivity` | Enable the control (off by default) |
| `--sound-sensitivity-position N` | Force tap `N` (0–99) at startup, overriding the saved value |
| `--sound-sensitivity-cs-pin N` | Move `CS` off BCM22 |
| `--sound-sensitivity-inc-pin N` | Move `INC` off BCM23 |
| `--sound-sensitivity-ud-pin N` | Move `U/D` off BCM24 |

### Step 4: Verify the Resistance with a Meter

Stop the server first — it holds the same GPIO lines — then sweep the wiper with
a multimeter on the two nodes:

```bash
uv run python scripts/hardware-test/test_x9c104.py --sweep
```

**Which number you should see depends on whether the pot is in circuit**, and
the sweep prints both:

| Measuring | Expect | Sweep column |
|-----------|--------|--------------|
| Pot alone, leads not yet on the pads | ~40 Ω → ~100 kΩ | `R17` |
| Soldered across R17, board attached | ~40 Ω → **~50 kΩ** | `preamp` |

In circuit the board's own 100 kΩ `R3` sits in parallel across those same two
pads, so the meter reads `R17 ∥ R3` — which tops out near 50 kΩ, not 100 kΩ.
That is correct and expected; it is not a half-broken pot. Measure the pot on
its own before soldering if you want to see the full 100 kΩ span.

**Power the detector down before measuring in circuit**, and treat any reading
above ~50 kΩ, or any reading that falls as the tap rises, as a bad measurement
rather than a bad pot — see
[meter readings do not track the tap](#digital-pot-meter-readings-do-not-track-the-tap).

What matters either way is that the reading **moves monotonically** with the
printed tap.

The sweep defaults to every 10th tap with a 2-second dwell, which is a quick
first look. To go finer and slower — one tap at a time, five seconds each, so
an auto-ranging meter has time to settle:

```bash
uv run python scripts/hardware-test/test_x9c104.py --sweep --sweep-step 1 --sweep-dwell 5
```

`--sweep-step 1` visits all 100 taps, so budget the time: total runtime is
roughly `stops × dwell`, about 8 minutes for that example. A middle ground of
`--sweep-step 5 --sweep-dwell 4` covers 21 stops in under 90 seconds and is
usually enough to prove the wiper moves smoothly.

To park it at one value instead of sweeping:

```bash
uv run python scripts/hardware-test/test_x9c104.py --position 46
```

### A Short That Looks Like Correct Wiring

`RH` tied to `RW` is right. What must **not** happen is a wire carrying that
joined node straight to `RL`, bypassing the pads — that shorts the element end
to end, and the meter reads a near-constant few tens of ohms at every tap.

The pads have to sit *in* the path:

```
  RH+RW ──── wire ──── R17 pad A
                          ⋮  (the SEN-14262 footprint)
  RL    ──── wire ──── R17 pad B
```

Two wires leave the pot, and they land on two different pads. If a sweep shows
a flat reading, this is the first thing to check.

### Step 5: Find the Ceiling for Your Room

A meter proves the resistance moved. It cannot tell you whether *sensitivity*
moved, which is the thing you actually care about. For that, sweep the wiper
while counting `GATE` edges on the trigger line:

```bash
uv run python scripts/hardware-test/test_x9c104.py --noise-floor
```

Run it in the room you hit in, with the room as quiet as it gets during play,
and do not hit anything while it runs. Each tap gets a dwell window:

```
  tap        R17   edges    high  verdict
  ---  ---------  ------  ------  ---------
    0       0.0k       0      0%  quiet
   10      10.1k       0      0%  quiet
   20      20.2k       0      0%  quiet
   30      30.3k       0      0%  quiet
   40      40.4k       3      2%  active
   ...

Ambient noise started firing the trigger at tap 40; backing off 5 taps lands on 35.
```

The tap where `active` first appears is where room noise alone fires the
trigger — the **ceiling**. The script backs off a few taps and parks the wiper
there.

Two verdicts other than `quiet` are worth knowing:

- **`active`** — the GATE is pulsing. Ambient noise is getting through.
- **`saturated`** — the GATE is high essentially the whole window. That is the
  classic over-gain failure, and it is invisible to an edge count alone: a
  latched-high line produces *no* transitions, so counting edges would rank the
  worst case as the quietest. If tap 0 is already saturated, gain is not the
  problem — go back to the wiring checks.

This only finds the ceiling. The **floor** — the tap below which real strikes
stop registering — needs actual strikes, which is Step 6.

### Step 6: Tune from the UI

Open **Debug → Sound** and use the **Sound Trigger Sensitivity** slider. The
readout under it shows the applied percentage, the resistance R17 now presents,
and the resulting preamp leg.

- **Triggering on ambient noise** (a door, a fan, a neighbouring bay) — turn it
  **down**.
- **Missing strikes**, or the GATE LED not flashing on a clean hit — turn it
  **up**.
- **GATE LED stuck on** at every setting — the gain is not the problem; go back
  to the wiring checks below.

To find the **floor**, start at the ceiling Step 5 gave you and walk down while
hitting shots, until strikes stop registering. Your working setting is between
the two. Watch **Debug → Status** while you do it: the trigger counters and
Last Trigger card show whether each strike was accepted, which is a faster
signal than waiting for a shot to appear.

If the two ends cross — ambient noise fires the trigger at a tap *below* where
strikes reliably register — no setting will work, and gain is not the lever.
Move the detector away from the noise source, or damp it.

**Recalibrate wiper** re-homes the wiper against the `RL` end and steps back to
the current position. The X9C104 cannot be read back, so the position OpenFlight
shows is a model of the chip rather than a measurement; if a brown-out or a
glitched line makes the two disagree, this resyncs them.

### What OpenFlight Stores, and Where

The setting is saved to `~/.config/openflight/sound_sensitivity.json` and
re-applied at every startup.

OpenFlight deliberately **never writes the X9C104's own non-volatile memory**.
That NVM is rated for about 100,000 stores, and saving on every slider move
would wear the part out for no benefit — the server re-homes and re-applies the
saved position on each boot anyway.

The practical consequence: if you power the detector **without** running
OpenFlight, its gain is whatever the chip last committed to NVM, which for a new
part is an arbitrary factory value. Anything that runs through the server —
including `scripts/start-kiosk.sh` — sets it correctly.

### Digipot Wiring Checklist

- [ ] X9C104 pin 8 (VCC) → Pi 5V (physical pin 2)
- [ ] X9C104 pin 4 (VSS) → Pi GND (physical pin 20, same rail as the detector)
- [ ] X9C104 pin 7 (CS) → Pi BCM22 (physical pin 15)
- [ ] X9C104 pin 1 (INC) → Pi BCM23 (physical pin 16)
- [ ] X9C104 pin 2 (U/D) → Pi BCM24 (physical pin 18)
- [ ] 10 kΩ from X9C104 pin 7 (CS) to Pi **3.3 V** — not to 5 V
- [ ] 100 nF ceramic across X9C104 pin 8 (`VCC`) and pin 4 (`VSS`), short leads, at the chip
- [ ] 100–330 Ω in series on `INC` (and ideally `CS` and `U/D`), at the Pi end
- [ ] `INC` lead short, and not bundled against `CS`/`U/D`
- [ ] X9C104 pin 3 (RH) linked to pin 5 (RW)
- [ ] RH+RW pair → one R17 pad; pin 6 (RL) → the other R17 pad
- [ ] No fixed resistor left soldered in R17 (it would parallel the pot)
- [ ] `--sound-sensitivity` passed to the server

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

### Digital pot: the wiper moves further than commanded

Symptom: commanding tap 10 lands somewhere much higher — say 33 kΩ instead of
10 kΩ — and any mid-range tap saturates at ~100 kΩ. The ratio is consistent
rather than random, so the chip is counting several edges for every `INC` pulse
sent.

Measure it: `--position 10 --hold` should read ~10 kΩ. Whatever it reads,
`tap = (ohms - 40) x 99 / 100000`, and `tap / 10` is the number of steps the
chip takes per pulse.

Note that **tap 0 always looks correct** even when this is happening. It is an
end stop, so `calibrate()` lands there whether the chip counts 99 pulses or 300.
Never conclude the wiring is good from tap 0 alone — check a mid-range tap.

Two causes, in the order worth testing:

1. **Logic level too close to the threshold.** Try powering the pot from the
   Pi's **3.3 V** (header pin 1 or 17) instead of 5 V. Parts specifying
   `VIH = 0.7 x VCC` need 3.5 V to read high at a 5 V supply, which the Pi's
   3.3 V output never reaches — the input hovers at its switching point and each
   transition crosses it several times. At a 3.3 V supply the threshold drops to
   2.31 V and the Pi drives it cleanly.
2. **No local decoupling** — check this first when the overshoot is consistent
   *and* does not respond to the supply rail or to a series resistor on `INC`.
   Both of those leave supply integrity untouched. Fit the
   [100 nF capacitor](#required-a-decoupling-capacitor) at the chip.
3. **Ringing or crosstalk on `INC`.** Fit
   [series resistors](#strongly-recommended-series-resistors-on-the-control-lines).
   A series resistor at the Pi end cannot help coupling picked up near the chip;
   for that, add 100 pF from `INC` to GND at the chip end.

`--step-delay-us` does **not** test for this. It changes the gap between pulses,
not the edge rate, and the ringing is over long before the next pulse starts.

### Digital pot: reads full scale (~100 kΩ) at every tap

The wiper is pinned at the `RH` end. The usual cause is a dead `U/D` line
(pin 2): with it stuck high, `calibrate()`'s decrements run *upward* instead,
driving the wiper to the top, and every later move can only push it further
into that end stop.

Confirm with two readings while the lines are held:

```bash
uv run python scripts/hardware-test/test_x9c104.py --position 0 --hold
```

- `RW` (pin 5) to `RL` (pin 6) should be **~40 Ω**. Full scale here means the
  wiper never came down.
- `U/D` (pin 2) to GND should be **~0 V** — calibrate leaves the direction low.
  3.3 V means BCM24 is not reaching pin 2.

Repeat with `--position 99 --hold`: `RW`–`RL` should be ~100 kΩ and `U/D` ~3.3 V.
If neither reading changes between the two runs, the line is not being driven.

> **The `RH`–`RW` tie hides one variant of this.** Tying them gives current a
> second path — `RH` through the whole element to `RL` — so an open wiper reads
> as a steady ~100 kΩ instead of an obvious open circuit. When bench-testing,
> lift the tie and measure pin 5 to pin 6 directly, then restore it for the
> build.

### Digital pot: a parked position does not hold

Symptom: `--position N` sets the wiper, but the resistance reverts as soon as
the command finishes.

This is expected without the [pull-up on `CS`](#strongly-recommended-a-pull-up-on-cs).
Releasing the GPIOs lets `CS` fall to the Pi's default pull-down, which selects
the chip, and the accompanying fall on `INC` is a step command. A selected chip
then keeps stepping on line noise, so the wiper drifts off the parked value.

Two fixes, and you want both eventually:

- **Now, to measure:** re-run with `--hold`. The script stays alive with the
  lines driven until you press Enter, so the wiper stays put.
  ```bash
  uv run python scripts/hardware-test/test_x9c104.py --position 46 --hold
  ```
- **Permanently:** fit the 10 kΩ `CS` pull-up to 3.3 V.

Note this does not affect normal operation. The server holds the lines for as
long as it runs, and re-applies the saved position at every startup.

### Digital pot: meter readings do not track the tap

Readings that jump around, fall as the tap rises, or exceed ~50 kΩ in circuit
are not a miswired pot — they are an invalid measurement. Two hard bounds make
this easy to spot:

- **In circuit, nothing can read above ~50 kΩ.** The board's 100 kΩ `R3` is
  across the same two pads, and a parallel combination never exceeds either
  branch.
- **The curve must be monotonic.** A pot wired backwards still rises or falls
  smoothly; a cold joint reads flat. Only an invalid measurement wanders.

The usual cause is measuring with the **SEN-14262 still powered**. R17 is in the
feedback path around a live op-amp, which drives that node itself, so an
ohmmeter reads the amplifier's response to room noise rather than a resistance.

Fix the measurement before drawing any conclusion about the wiring:

1. Power the detector down completely — disconnected from 3.3 V, not just idle.
2. Better still, lift one pot lead off the pads so nothing is in parallel.
3. Probe the pot's own pins (5 and 6), not the pads.

Then check the two endpoints rather than sweeping. A sweep only means something
once these are right:

```bash
uv run python scripts/hardware-test/test_x9c104.py --position 0   # RW-RL ~40 ohm
uv run python scripts/hardware-test/test_x9c104.py --position 99  # RW-RL ~100 kohm
```

Tap 0 not reading near zero means the wiper is not moving — a control-line
fault, not a pad fault. `RH`-`RL` (pins 3 to 6) is a useful third reading: it is
the whole element, independent of the wiper, and should be ~100 kΩ at every tap.

### Digital pot: the sweep never leaves "quiet" at any tap

Ambient noise never reaches the trigger even at full gain. That is a fine
result in a quiet room — take the top of the range and confirm strikes still
register. If strikes do not register either, the GATE path is the problem, not
the gain: check `GATE` → `HOST_INT`, and that the detector's LED flashes when
you clap.

### Digital pot: slider moves but the detector does not change

The control lines are working (the server would have errored otherwise), so the
problem is on the resistor side.

1. Stop the server and sweep with a meter across the R17 pads:
   `uv run python scripts/hardware-test/test_x9c104.py --sweep`. A reading stuck
   at one value means a cold joint on `RW`, `RL`, or an R17 pad — or the
   `RH+RW` node wired straight to `RL` instead of through the pads.
   In circuit the top of the range is ~50 kΩ, not 100 kΩ; that is `R17 ∥ R3`
   and is expected.
2. Check no fixed resistor is still soldered into R17 — it parallels the pot and
   flattens its range.

### Digital pot: turning sensitivity up makes it less sensitive

`RH` (pin 3) is wired to an R17 pad in place of `RL` (pin 6). Move the pad lead
to pin 6 and keep pin 3 linked to pin 5.

### Digital pot: the position drifts from what the UI shows

The X9C104 has no readback, so the displayed position is a model of the chip. A
brown-out or a glitched control line can desynchronise them. Press
**Recalibrate wiper** on the Debug page, or restart the server — both re-home
the wiper against the `RL` end.
