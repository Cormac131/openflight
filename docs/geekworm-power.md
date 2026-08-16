# Geekworm Power Display

OpenFlight can show battery and external-power status for the Geekworm X1202
and X1206 UPS boards. Monitoring is opt-in and uses the same interface on both
models:

- MAX17043-compatible fuel gauge at I2C address `0x36`
- GPIO6 high when an external power adapter is present

OpenFlight only reads these signals. It does not control charging through
GPIO16 and does not automatically shut down at a low battery level.

## Hardware setup

Install the Raspberry Pi 5 on the UPS pogo pins and connect all external power
and powered peripherals to the Geekworm board. Do not power the Pi through its
own USB-C port while using the UPS.

Enable I2C with `sudo raspi-config`, then reboot. To verify the fuel gauge:

```bash
sudo i2cdetect -y 1
```

The scan should show a device at `36`. If it does not, power down and check the
pogo-pin contact for GPIO2 and GPIO3 before starting OpenFlight.

OpenFlight prefers Linux's native power-supply devices when they are enabled.
Add these lines to `/boot/firmware/config.txt` and reboot:

```ini
dtoverlay=i2c-sensor,max17040
dtoverlay=gpio-charger,gpio=6,active_low=0,gpio_pull=down,type=mains
```

The first overlay exposes the Geekworm fuel gauge as a `Battery` under
`/sys/class/power_supply`. The second exposes GPIO6 as an active-high `Mains`
source. OpenFlight automatically uses both native devices when present and
falls back to direct I2C/GPIO access otherwise.

The native driver and direct fallback both use the fuel gauge's SOC register
for percentage. The Geekworm LEDs are coarser voltage bands and can change
before the modeled SOC percentage while charging or under load.

## Start OpenFlight

```bash
scripts/start-kiosk.sh --geekworm-power
```

With the flag enabled, the header shows the battery percentage and whether
external power is connected. OpenFlight displays dismissible warnings while
discharging at 20% and 10%. Plugging in external power, recovering above the
threshold, or restarting the UI starts a new warning episode.

If I2C or GPIO telemetry fails, OpenFlight continues running and shows an
unavailable power indicator. The monitor retries automatically every five
seconds.

## Session logs

The session JSONL includes `power_status` entries on startup, power-state
changes, warning-threshold changes, telemetry failures or recovery, and at
least once per minute while the state is unchanged. Each entry includes:

- `state`
- `battery_percent`
- `battery_voltage_v`
- `external_power`
- `available`
- `error`
- `updated_at`

When `--geekworm-power` is absent, OpenFlight does not access the UPS hardware
or show a power indicator. A Pi powered directly from its official adapter has
no battery telemetry, so the power display remains hidden in that setup.
