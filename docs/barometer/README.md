# BMP580 Barometer Setup

OpenFlight can use a BMP580 barometer mounted near the rig to measure station
pressure and temperature. Those two numbers give air density, and air density
sets how far the ball carries.

This is an optional feature. It is disabled unless `--barometer` is passed.
Without it, OpenFlight uses the elevation and temperature you configure on the
command line; without those either, it assumes standard sea-level air.

**With the sensor fitted, nothing needs configuring.** Station pressure already
contains your site elevation, so `--barometer` on its own is a complete setup.
If the sensor later stops responding, OpenFlight reuses its last reading rather
than reverting to sea level, so the rig stays correct through a failure.

## Is This Worth Fitting?

Read this before buying anything. Air density is worth a lot; the sensor is
worth much less than the software behind it.

Measured with OpenFlight's own trajectory model, relative to standard sea-level
air:

| Scenario | Driver | 7-iron | PW |
|---|---|---|---|
| Sea level, 0 °C winter | −4.3 | −3.6 | −3.0 |
| Sea level, 35 °C summer | +5.0 | +4.2 | +3.6 |
| Deep low, 980 hPa | +2.5 | +2.1 | +1.8 |
| Strong high, 1035 hPa | −1.7 | −1.4 | −1.2 |
| 1000 ft | +2.7 | +2.3 | +2.0 |
| 5280 ft (Denver), 20 °C | **+14.0** | +12.1 | +10.6 |

Split by which input drives the error:

| Term | Worth (driver) | What it needs |
|---|---|---|
| Elevation | up to 14 yd | A number you type once. It never changes. |
| Temperature | ~9 yd seasonally | A reading, or a number you update. |
| **Barometric weather** | **±1 to 2.5 yd** | **A barometer. This is the only term the sensor uniquely buys.** |
| Humidity | 0.7–1.4 yd | A humidity sensor. The BMP580 has none. |

Two ways to collect this. `--elevation-ft` and `--air-temp-c` cost nothing and
capture the top two rows, but the temperature is a number you have to keep
updating as the seasons turn. `--barometer` captures all three measurable rows
and never needs updating — which, as much as the ±1–2.5 yd weather term, is the
practical reason to fit one.

## What To Buy

| Part | Product |
|------|---------|
| BMP580 breakout | [Adafruit BMP580 Precision Barometric Pressure Sensor, product 6413](https://www.adafruit.com/product/6413) |
| Solderless cable kit | [4-pin JST SH 1.0 mm STEMMA QT/Qwiic cable kit on Amazon](https://www.amazon.com/Connector-Compatible-Development-Sensors-Drivers/dp/B0GJPRX4YT) |
| Mounting | Thin double-sided mounting tape or nonconductive standoffs |

This is the same STEMMA QT cable kit used for the LIS3DH inclinometer, so it
connects to the Pi header without soldering.

The driver also accepts a BMP581 or BMP585 breakout. All three share one
register map and differ only in chip ID.

Do not buy a bare BMP580 chip. Use a breakout with the regulator, I2C pull-ups,
and STEMMA QT/Qwiic connectors already installed.

> [!NOTE]
> The BMP580's headline precision is wasted here. Its ±0.06 hPa relative
> accuracy works out to 0.006% air density, about 0.002 yd of carry. Even a
> ±1 hPa sensor gives 0.3 yd. If you already own a BMP280, BMP390, or BME280,
> a small driver change would serve just as well — precision is not the
> limiting factor. Mounting temperature is.

## How Density Is Computed

Air density comes from the ideal gas law:

```text
density = station pressure / (287.0528 * (temperature_C + 273.15))
```

That is it. Note what does **not** appear: altitude.

This matters because every barometer library exposes an `altitude` reading, and
it is the one output to ignore. Altitude is not measured. It is computed from
the pressure the sensor did measure, by *assuming* a sea-level reference
pressure — the drivers hardcode 1013.25 hPa. A sensor that never moves
therefore reports an altitude that wanders with the weather:

| Actual sea-level pressure | Reported "altitude" | True density at 15 °C |
|---|---|---|
| 980 hPa | +920 ft | 1.185 |
| 995 hPa | +502 ft | 1.203 |
| 1013.25 hPa | 0 ft | 1.225 |
| 1025 hPa | −319 ft | 1.239 |
| 1035 hPa | −589 ft | 1.251 |

The density column is exact at every row, while the altitude column is off by
over 1500 ft across the range. OpenFlight uses the measured pressure directly
and never routes it through an altitude.

This is also why a barometer needs no elevation configured. The sensor cannot
tell you how high you are — but it does not need to, because the pressure it
measures is *already* lower at altitude, and that is the only thing carry
depends on. What pressure alone cannot do is *separate* elevation from weather,
and that separation is a question the carry model never asks.

`--elevation-ft` therefore does different jobs in the two setups: with no sensor
it is how OpenFlight estimates your pressure, and with one fitted it is
log metadata only. It never enters the density calculation alongside a real
pressure reading.

## Power Down Before Wiring

Shut down the Pi and remove power before connecting or moving GPIO wires. A
misplaced 3.3 V lead can short the Pi, cause reboot loops or a black screen, and
potentially damage the Pi or sensor.

## Wiring

The two STEMMA QT connectors on the Adafruit BMP580 are electrically identical.
Use whichever port gives the cable the cleanest route.

| Cable color | Signal | Raspberry Pi physical pin | Pi signal |
|-------------|--------|---------------------------|-----------|
| Red | `VIN` / `3.3V` | **17** | 3.3 V power |
| Black | `GND` | **20** | Ground |
| Blue | `SDA` | **3** | GPIO2 / I2C SDA |
| Yellow | `SCL` | **5** | GPIO3 / I2C SCL |

```text
BMP580 STEMMA QT                         Raspberry Pi GPIO header

Red     VIN / 3.3V  ------------------>  physical pin 17 (3.3V)
Black   GND         ------------------>  physical pin 20 (GND)
Blue    SDA         ------------------>  physical pin 3  (GPIO2/SDA)
Yellow  SCL         ------------------>  physical pin 5  (GPIO3/SCL)
```

> [!WARNING]
> Use physical pin numbers exactly as shown. GPIO/BCM numbers are a different
> numbering system. Do not connect the BMP580 to a 5 V GPIO-header pin.

Cable colors are not a universal standard. If using a different cable, verify
each conductor against the breakout labels before powering the Pi.

The BMP580 shares the I2C bus with the LIS3DH inclinometer without conflict:
the LIS3DH sits at `0x18` and the BMP580 at `0x47`. Both can hang off the same
Pi power and ground pins as long as the connections are secure.

## Mounting

**This is the part that decides whether the sensor helps or hurts.**

The pressure channel is insensitive to where you put the sensor — no enclosure
is airtight, so it reads true station pressure anywhere. The temperature channel
is not. A sensor die inside a warm enclosure reads several degrees above ambient
from Pi self-heating, and:

- 3 °C of temperature error ≈ 1% air density ≈ **0.8 yd of driver carry**
- That is the same size as the entire weather benefit the sensor was fitted for

So an uncalibrated sensor tucked next to the Pi can leave you worse off than
typing a temperature in by hand. Either:

1. **Mount it in moving ambient air** — outside the enclosure, or in a vented
   position away from the Pi, the radar, and any regulator; or
2. **Calibrate the offset** with `calibrate_bmp580.py` (below) and pass
   `--barometer-temp-offset-c`.

Doing both is better than either.

Keep the board away from direct sun and from anything that blows air across it.

## Enable I2C On The Pi

Enable the Pi header I2C interface and reboot:

```bash
sudo raspi-config nonint do_i2c 0
sudo reboot
```

After reconnecting over SSH, verify that bus 1 exists:

```bash
ls -l /dev/i2c-1
```

To scan the bus, install `i2c-tools` if needed and run:

```bash
sudo apt-get install -y i2c-tools
i2cdetect -y 1
```

The default BMP580 address is `0x47`, so the scan should contain `47`:

```text
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
40: -- -- -- -- -- -- -- 47 -- -- -- -- -- -- -- --
```

With an inclinometer also fitted, expect both `18` and `47`.

## Install Software

From the OpenFlight checkout:

```bash
uv sync
```

The Linux installation includes `smbus2`, which the BMP580 driver uses to talk
to `/dev/i2c-1`.

## Verify Raw Readings

Run the standalone hardware readout before starting the full application:

```bash
uv run python scripts/hardware-test/read_bmp580.py
```

Example output:

```text
BMP580/BMP581 detected on I2C-1 at 0x47
P=  1004.31hPa  Traw=+27.40C  T=+27.40C  rho=1.1651kg/m3
```

Useful options:

```bash
# Print 10 readings and exit
uv run python scripts/hardware-test/read_bmp580.py --count 10

# Apply a calibrated temperature offset
uv run python scripts/hardware-test/read_bmp580.py --temp-offset-c -4.5

# Compare the measurement against the configured-only density it replaces
uv run python scripts/hardware-test/read_bmp580.py --elevation-ft 5280

# Probe the alternate address while troubleshooting
uv run python scripts/hardware-test/read_bmp580.py --address 0x46
```

Sanity check the pressure against a local weather report. Forecasts quote
*sea-level* pressure, so at altitude your station pressure reads lower — about
33 hPa per 1000 ft. At 5280 ft a 1013 hPa forecast means roughly 834 hPa on the
sensor.

## Calibrate The Temperature Offset

The offset corrects the difference between the BMP580's die temperature and the
true air temperature, which is dominated by enclosure self-heating.

1. Assemble the rig exactly as it will run, and start it.
2. **Let it reach normal running temperature** — at least 20 minutes. Calibrating
   a cold rig bakes in an offset that vanishes once the Pi warms up.
3. Measure true air temperature next to the rig with a separate thermometer.
4. Pass that reading to `--reference-temp-c`.

```bash
uv run python scripts/hardware-test/calibrate_bmp580.py --reference-temp-c 21.5
```

The script averages 30 samples and prints something like:

```text
Raw temperature mean: +26.180C (std dev 0.084C)
Reference temperature: +21.500C
Mean pressure: 1004.31 hPa
Uncorrected density 1.1670 vs corrected 1.1852 kg/m3 (1.53% error, about 1.1 yd of driver carry)
Recommended flag: --barometer-temp-offset-c -4.68
```

Record that value in the launch command. The current implementation does not
write a calibration file.

If the script warns that temperature is still drifting, let the rig settle
longer and run it again.

## Start OpenFlight

Example production startup:

```bash
scripts/start-kiosk.sh --barometer
```

That is the whole configuration. **No elevation, temperature, or pressure
number is needed with a sensor fitted** — station pressure already contains the
site elevation, so the measurement supplies every input the carry model wants.

Add the temperature offset once you have calibrated it:

```bash
scripts/start-kiosk.sh --barometer --barometer-temp-offset-c -4.7
```

`--elevation-ft` and `--air-temp-c` remain available, but with a barometer
fitted they are overrides, not requirements. If the sensor stops responding,
OpenFlight keeps using its last reading rather than reverting to sea level, so
an unconfigured altitude rig stays correct through a sensor failure.

At startup, OpenFlight prints the measured pressure, temperature, and density,
alongside the configured density the sensor is replacing. A large gap between
those two on a rig at the right elevation usually means an uncalibrated
temperature offset.

For each shot, OpenFlight:

1. Takes the newest barometer reading, if one is not stale.
2. Computes air density from its pressure and corrected temperature.
3. Simulates carry twice: once at the normalization density, once in that air.
4. Reports the normalized carry as the headline and the actual-conditions carry
   alongside it.

Air conditions are resolved best-source-first, so nothing has to be configured:

| Source | When | Logged as |
|---|---|---|
| Fresh sensor reading | Normal operation | `sensor` |
| That sensor's last reading, however old | Sensor stopped responding | `sensor_stale` |
| Configured elevation and temperature | No sensor, or it never read | `config` |
| Standard sea level | Nothing configured and no sensor | `standard` |

The second tier is what makes the sensor self-sufficient. Air density at a fixed
site moves a few percent over days, so a remembered reading stays far closer to
the truth than a textbook assumption — and it still carries the site elevation
implicitly. Without it, a failed sensor on an unconfigured rig would silently
revert to sea level and lose the full 14 yd at altitude.

## Two Carries

With any air configuration in play, shots carry two numbers:

- **`carry_spin_adjusted`** — the headline, normalized to a fixed density so it
  stays comparable across sessions and against published launch-monitor data.
- **`carry_actual_yards`** — the same shot in the air you are standing in, shown
  as the smaller line under the carry tile.

Simulator connectors are unaffected: GSPro, E6, and the rest model their own
weather and altitude, so sending them a density-adjusted carry would count the
correction twice.

Set `--carry-normalization-density 1.184` if you want the headline number
comparable to TrackMan "Flat" figures instead of standard sea level.

## Session Logging

The `session_start` entry records:

- Whether the barometer initialized.
- I2C bus and address.
- Sampling rate and temperature offset.
- Startup reading or initialization error.

Each `shot_detected` entry records:

- `carry_actual_yards` alongside `carry_spin_adjusted`.
- `air_density_kg_m3` used for that shot.
- `air_conditions_source`: `sensor`, `sensor_stale`, `config`, or `standard`.

That last field matters when reading logs later: the difference between an
assumed density and a measured one is up to 14 yd, far too large to leave
implicit.

## Troubleshooting

### `0x47` Is Missing From `i2cdetect`

1. Confirm `/dev/i2c-1` exists.
2. Confirm I2C is enabled, then reboot.
3. Recheck blue to physical pin 3 and yellow to physical pin 5.
4. Confirm black is on ground and red is on 3.3 V.
5. Reseat both ends of the JST-SH cable.
6. Try the other identical STEMMA QT port on the breakout.
7. Run `i2cdetect -y 1` again.

If the scan shows `0x46`, the board's SDO input is pulled low. Pass
`--barometer-address 0x46`.

### `CHIP_ID expected one of 0x50, 0x51`

OpenFlight reached an I2C device at the selected address, but it did not
identify as a BMP580, BMP581, or BMP585. Check for another device at `0x47`,
verify the breakout model, and inspect the bus with `i2cdetect -y 1`.

### `NVM not ready or reporting an error`

The sensor answered but its calibration memory is not healthy, so its readings
cannot be trusted. Power-cycle the Pi. If it persists, the breakout is
suspect — do not run with it, since it will silently produce wrong densities.

### `conversion did not complete`

The sensor accepted a measurement request but never signalled data ready.
Usually a marginal connection. Reseat the cable and check for a second device
fighting for the same address.

### Permission Denied On `/dev/i2c-1`

Add the OpenFlight user to the `i2c` group, then log out and back in or reboot:

```bash
sudo usermod -aG i2c "$USER"
sudo reboot
```

### Carry Numbers Moved After Fitting The Sensor

Expected, and the point of the exercise — but check the direction. Compare
`read_bmp580.py --elevation-ft <yours>` against your local forecast pressure. If
the measured density is more than about 2% off the configured value at the
correct elevation, the temperature offset is the usual culprit.

### Temperature Reads Several Degrees High

That is enclosure self-heating, and it is the normal failure mode. See
[Mounting](#mounting) and calibrate the offset. Do not ignore it: it is worth
about a yard of driver carry, which is the entire benefit of the sensor.

### Shots Report `sensor_stale`

No fresh reading within `max_reading_age_s` (default 300 s), so OpenFlight is
reusing the sensor's last measurement. Carry stays close to correct, but the
sensor has stopped responding — check the I2C connection and the logs for
sample errors.

### Pressure Looks Wildly Wrong

Readings outside 400–1150 hPa are rejected rather than converted into a
density — a disconnected sensor reading zeroes must not become "very thin air".
The service logs the rejection and keeps the previous reading until it goes
stale.

## Current Limitations

- No humidity channel. The BMP580 does not measure it; worth 0.7–1.4 yd of
  driver carry at hot, saturated extremes.
- The temperature offset is a fixed number, not a model. A rig whose enclosure
  temperature varies a lot with load will drift from any single calibration.
- Sensor bus, address, and calibration are command-line/runtime settings rather
  than a persisted rig configuration file.
- Readings are not correlated to impact time the way inclinometer snapshots are.
  Air density moves over minutes, so the newest reading is used regardless of
  when in the sampling cycle the shot landed.
