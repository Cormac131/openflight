# OpenFlight Parts List

Hardware components for building the OpenFlight golf launch monitor.

> **Ordering shortcut:** A shared **[OpenFlight Mouser project](https://www.mouser.com/en/Tools/Project/Share?AccessID=4c97a00bbc)** is available for the parts Mouser stocks — open it, save it to your own Mouser account, and add the whole list to your cart in one step instead of searching for each item. Check it against the tables below before you order: anything Mouser does not carry has a direct vendor link here.

> **Next step after gathering parts:** See the [Raspberry Pi Setup Guide](raspberry-pi-setup.md) for assembly and software installation.

## Core Components

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **OPS243 Radar** | Doppler radar for ball/club speed detection | [OmniPreSense](https://omnipresense.com/product/ops243-doppler-radar-sensor/) | $249 |
| **Raspberry Pi 5** | Main compute unit (4GB+ recommended) | [Adafruit](https://www.adafruit.com/product/5812) | $130 |
| **7" Touchscreen Display** | HMTECH 7" 1024x600 IPS display | [Amazon](https://www.amazon.com/dp/B0D3QB7X4Z) | $46 |

> **NOTE on OPS243-A-W (WiFi version):** The standard **OPS243-A** (USB only) is strongly recommended. The WiFi module on the OPS243-A-W drives the internal UART receive line, preventing direct connection to the Raspberry Pi GPIO UART (Layout A). However, if you already have the WiFi version, it can still be used over USB with a powered USB hub (Layout B) when paired with the IWR6843 angle radar.

> **Display alternative:** The [Raspberry Pi Touch Display 2](https://www.raspberrypi.com/products/touch-display-2/) (7" 720x1280, MIPI DSI) also works with the Pi 5. If you use it, print the `Touch_Display2_backplate.stl` and `Touch_Display2_shell.stl` from the IARC case instead of `monitor_shell.stl` — see the [IARC case instructions](../cad/IARC_case/README.md).

## Sound Trigger (for Rolling Buffer Mode)

The sound trigger detects club impact to precisely time radar captures. Essential for spin detection via rolling buffer mode.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **SparkFun SEN-14262** | Sound Detector with envelope/gate outputs | [SparkFun](https://www.sparkfun.com/products/14262) | $12 |
| **Through-hole resistor** | For R17 pad on SEN-14262 to reduce sensitivity (see note) | Any electronics supplier | $1 |
| **Jumper Wires** | 3 wires: GATE → HOST_INT, VCC → 3.3V, GND → GND | Any | $5 |

> **R17 resistor:** The SEN-14262 is rated for 5V but runs at 3.3V in this setup, which can cause the GATE output to stick high. Soldering a resistor into the R17 through-hole position (in parallel with the onboard 100kΩ R3) reduces preamp gain and fixes this. Start with 47kΩ; use a lower value (e.g. 33kΩ) if the sensor is still too sensitive for your environment.

### Optional: Software-Adjustable Sensitivity (Digital Pot)

A fixed R17 resistor locks the detector at one gain. Replacing it with an I2C
digital potentiometer makes that resistance
software-controlled, so you tune sensitivity with a slider on the **Debug →
Sound** page rather than with a soldering iron. Useful if you hit in more than
one place — a garage, a range bay, and a quiet basement all want different gain.

This is entirely optional. A build with a soldered R17 works exactly as before.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **Microchip MCP4017T-104E/LT** | **Preferred.** I2C 100 kΩ true rheostat, SC70-6. Its floating `B`/`W` terminals suit the R17 feedback pad | [Microchip](https://www.microchip.com/en-us/product/mcp4017) | $1 |
| **Wiring** | 4 wires: `VDD`/`GND`/`SDA`/`SCL` to the Pi, plus 2 short leads from `B` and `W` to the R17 pads | Any | $1 |

Do not substitute an MCP4018/MCP4019 breakout unless the meter check below
proves the two terminals you plan to use are floating from ground. Many
breakouts expose the same I2C protocol but use a grounded-terminal variant,
which is not safe in the R17 feedback position.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **Verified floating 100 kΩ MCP401X breakout** | Optional convenience board only after Step 0 confirms no wiper-to-ground path. Two Qwiic ports are useful, but terminal topology matters more than the connector | Board-specific docs | Varies |
| **Wiring** | One JST-SH Qwiic cable if present, plus 2 short leads from the verified floating terminals to the R17 pads | Any | $2 |

Or, if you already have one:

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **Adafruit DS3502** | I2C 10 kΩ digital potentiometer with STEMMA QT / Qwiic | [Adafruit product 4286](https://www.adafruit.com/product/4286) | $5 |
| **Series resistor** | **Required with the DS3502** — 33 kΩ typical; shifts its 10 kΩ span onto R17's operating range | Any electronics supplier | $1 |
| **Wiring** | Either 5 Dupont jumpers, or one JST-SH STEMMA QT cable chained off the LIS3DH plus a single jumper for `V+` | Any | $1–5 |

> **⚠ Check the wiper-to-ground path before soldering.** The MCP401X family
> shares an address, a protocol and this codebase, but not its terminals: per
> Microchip, the -4018 and -4019 have terminal `B` connected internally to
> ground, where the -4017 is a true rheostat with `B` and `W` on pins. R17 sits
> in the preamp's feedback path, where neither end is at ground, so only a
> floating pair works here.
> **Two minutes with a meter settles it** —
> [Step 0](sound-trigger-wiring.md#step-0-check-the-wiper-to-ground-path) is the
> check, and a bare MCP4017 or the DS3502 is the fallback if a board fails it.

> **Why 100 kΩ matters.** The 10 kΩ DS3502 needs a series resistor to reach
> R17's 33–47 kΩ range at all, and that leaves the optional
> [closed-loop auto gain](sound-trigger-wiring.md#optional-closed-loop-auto-gain-ads1115)
> only ~1.2× of travel to work with. A 100 kΩ part needs nothing in series and
> gives the loop real authority.

> **It claims no GPIOs.** Either part shares the I2C bus the inclinometer and
> any UPS fuel gauge already use — the MCP401X at 0x2f (fixed in silicon, so
> only one can be on the bus), the DS3502 at 0x28. Qwiic and STEMMA QT are the
> same connector, so with the LIS3DH fitted one cable covers power and I2C. On
> a DS3502 `V+` still needs its own wire, as the JST cable does not carry it;
> the MCP401X has no such pin.

#### Optional: Closed-Loop Auto Gain

Adding an **ADS1115** ADC on the detector's `ENVELOPE` output lets the software
tune the gain itself, adjusting the pot between shots to keep envelope peaks in
a target band. It chains off the same STEMMA QT cable and sits at 0x48.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **ADS1115 breakout** | 16-bit I2C ADC reading the SEN-14262 `ENVELOPE` output | [Adafruit product 1085](https://www.adafruit.com/product/1085) | $15 |
| **Jumper wire** | 1 wire: `ENVELOPE` → ADS1115 `A0` | Any | $1 |

> **It is a trim, not a wide-range AGC.** R17 works against the fixed 100 kΩ R3,
> so the pot's whole travel moves the gain by only ~1.2× with the default 33 kΩ
> series resistor — less than the default 60–80% target band spans. Give the
> loop authority by narrowing the band or fitting a smaller series resistor;
> the wiring guide has the numbers.

Wiring and setup are in
[sound-trigger-wiring.md](sound-trigger-wiring.md#optional-software-controlled-sensitivity-digital-pot).

## Angle Radar (TI IWR6843) — CURRENT

This is the supported angle radar. It measures vertical and horizontal launch
angle, and supplies the pre-impact frames club path is derived from.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **TI IWR6843LEVM** | 60 GHz mmWave evaluation board, 4 RX × 3 TX | [TI](https://www.ti.com/tool/IWR6843LEVM) | $150 |
| **USB cable (data-capable)** | Connects the LEVM's CP2105 serial bridge to the Pi. Charge-only cables will not enumerate — check the connector on your board revision | Any | $5 |
| **Jumper wire** | 1 wire: detector `GATE` → Pi BCM17 / physical pin 11, alongside the existing `GATE` → OPS `HOST_INT` | Any | $1 |

The board needs **custom firmware** — it does not work out of the box. The
stock TI demo does not expose the raw radar cube OpenFlight needs. A validated
prebuilt image ships in `firmware/releases/`, so you do not need the TI
toolchain to flash it.

You also need physical access to the board's **boot-mode switch (S1.1)** and
**RESET button** to flash. Both are on the LEVM itself; nothing to buy.

### IWR6843 Setup

Two connection layouts are supported, and which one you can use depends on your
OPS243 variant:

| Layout | OPS243 connection | Extra parts needed |
|--------|-------------------|--------------------|
| **A (validated)** | Pi GPIO UART header | 4 jumper wires (5V, GND, TX, RX) |
| **B** | Powered USB hub | [Powered USB hub](https://www.amazon.com/dp/B0CN3F9Y1Z) (~$20) |

Layout A keeps the TI board on USB and moves the OPS243 to the Pi's GPIO
header, which is what the power budget requires — the Pi cannot supply both
radars over USB.

> [!WARNING]
> Layout A does **not** work with a **WiFi-equipped OPS243-A**. Its onboard WiFi
> module already drives the radar's UART receive line, so the Pi cannot send it
> commands. WiFi OPS boards must use Layout B with a powered hub.

Full instructions: **[IWR6843 Operator Guide](iwr6843/README.md)** for wiring,
flashing, mounting, and geometry; **[Moving the OPS243 to the Pi GPIO
UART](ops243-uart-migration.md)** for the OPS side of Layout A.

### Optional Enclosure Inclinometer

An LIS3DH mounted to the enclosure base lets OpenFlight compensate the IWR6843
tilt when the rig is placed on uneven ground.

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **Adafruit LIS3DH breakout** | Triple-axis accelerometer with STEMMA QT connectors | [Adafruit product 2809](https://www.adafruit.com/product/2809) | $5 |
| **JST-SH cable kit** | Solderless STEMMA QT/Qwiic to female Dupont wiring used in the validated build | [Amazon](https://www.amazon.com/Connector-Compatible-Development-Sensors-Drivers/dp/B0GJPRX4YT) | ~$10 |

See the **[LIS3DH Inclinometer Setup Guide](inclinometer/README.md)** for wiring,
mounting, calibration, startup flags, and troubleshooting.

---

## Angle Radar (K-LD7) — DEPRECATED

> **⚠️ DEPRECATED — do not buy for new builds.** The K-LD7 angle radars have been superseded by a more capable radar chip. K-LD7 support remains in the software for existing builds but will not receive further development. The parts below are listed for reference only.

Two K-LD7 modules measure launch angle (vertical) and club path / aim direction (horizontal). The OPS243 handles speed; the K-LD7s provide **angle and distance only** (speed data aliases above 62 mph).

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **RFbeam K-LD7 (×2)** | 24 GHz FMCW radar for angle + distance | [RFbeam](https://rfbeam.ch/product/k-ld7-radar-transceiver/) | ~$60 ea |
| **FTDI USB-to-Serial adapter (×2)** | 3.3V FTDI board for K-LD7 UART (e.g. FT232RL) | [Amazon](https://www.amazon.com/s?k=ftdi+3.3v+usb+serial) | ~$10 |

> **EVAL board not required.** The K-LD7 bare module communicates over 3.3V UART (TX, RX, VCC, GND). Any 3.3V FTDI USB-to-serial adapter works. The official K-LD7 EVAL board (~$120 each) is only needed if you want the RFbeam GUI software for configuration — OpenFlight configures the radar over serial automatically.

### K-LD7 Connection

Each K-LD7 connects via a 3.3V FTDI adapter, appearing as `/dev/ttyUSB*` on Linux.

```
K-LD7 Module (UART) → FTDI 3.3V Adapter → USB → Raspberry Pi
```

One unit is mounted vertically (launch angle), one horizontally (club path / aim direction). A `--kld7-angle-offset` parameter corrects for mounting geometry — see the [setup guide](raspberry-pi-setup.md) for calibration.

## Power & Accessories

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| **27W USB-C Power Supply** | Official Pi 5 power supply (5V 5A) | [Adafruit](https://www.adafruit.com/product/5814) | $14 |
| MicroSD Card (32GB+) | For Pi OS and software | Any Class 10 | $10 |
| USB-A to Micro-USB Cable | For OPS243 radar connection | Any | $5 |

## Optional

| Part | Description | Link | ~Price |
|------|-------------|------|--------|
| Tripod Mount | For positioning the unit | 1/4"-20 mount | $10 |
| **Geekworm X1202 UPS HAT** | Rechargeable Pi 5 power using four matching flat-top 18650 Li-ion cells. Cells are not included | [Geekworm](https://geekworm.com/products/x1202) | ~$48 + cells |
| **Geekworm X1206 UPS HAT** | Larger rechargeable Pi 5 power option using four matching 21700 Li-ion cells, advertised up to 20,000mAh total. Cells are not included | [Geekworm](https://geekworm.com/products/x1206) | Varies + cells |
| **InnoMaker OV9281 global-shutter camera** | High-speed monochrome camera for experimental vision work. Camera software is not enabled in the production kiosk path | [Amazon](https://www.amazon.com/dp/B09WTP5GZH?th=1) | ~$30 |

See [Camera and YOLO Experiments](yolo-performance-tuning.md) before buying the
camera; the standard setup does not install its optional software dependencies.

---

## Cost Summary

| Category | ~Price |
|----------|--------|
| Core (OPS243, Pi 5, Display) | $355 |
| Sound Trigger (SEN-14262 + resistor + wires) | $18 |
| Software sensitivity control (digital pot + wiring) — **optional** | $2–8 |
| Closed-loop auto gain (ADS1115 + wire) — **optional** | $16 |
| Power & Accessories | $27 |
| **Subtotal, no angle radar** | **~$400** |
| Angle Radar (IWR6843LEVM + cable + wire) — **current** | $156 |
| **Total with angle radar** | **~$556** |
| Angle Radar (2× K-LD7 + FTDI adapters) — **deprecated** | $140 |

OpenFlight works without any angle radar: you get ball speed, club speed, smash
factor, spin rate, and estimated carry. The angle radar adds measured launch
angle (vertical and horizontal) and is what club path is derived from.

If you are building new, buy the **IWR6843**, not the K-LD7s. It costs about the
same as the two K-LD7s plus their FTDI adapters ($156 vs $140) and replaces both
of them with one board. The K-LD7 path is **deprecated** and kept only so
existing builds keep working.
