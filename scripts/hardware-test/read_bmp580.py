#!/usr/bin/env python3
"""Print BMP580 station pressure, temperature, and the resulting air density."""

import argparse
import time

from openflight.air_density import AirConditions, air_density
from openflight.barometer import BMP580


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", type=int, default=1, help="I2C bus number (default: 1)")
    parser.add_argument("--address", type=lambda value: int(value, 0), default=0x47)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--count", type=int, default=0, help="Readings to print; 0 runs forever")
    parser.add_argument(
        "--temp-offset-c",
        type=float,
        default=0.0,
        help="Degrees added to the raw BMP580 temperature (default: 0)",
    )
    parser.add_argument(
        "--elevation-ft",
        type=float,
        default=None,
        help=(
            "Site elevation; enables a comparison against the configured-only "
            "density the sensor is replacing"
        ),
    )
    args = parser.parse_args()

    sensor = BMP580(bus_number=args.bus, address=args.address)
    sensor.initialize()
    print(f"{sensor.chip_name} detected on I2C-{args.bus} at 0x{args.address:02x}")

    configured = None
    if args.elevation_ft is not None:
        configured = AirConditions.from_elevation(elevation_ft=args.elevation_ft)
        print(
            f"Configured-only density at {args.elevation_ft:.0f} ft: "
            f"{configured.density_kg_m3:.4f} kg/m3"
        )

    try:
        index = 0
        while args.count == 0 or index < args.count:
            sample = sensor.read()
            temperature_c = sample.temperature_c + args.temp_offset_c
            density = air_density(sample.pressure_pa, temperature_c)
            output = (
                f"P={sample.pressure_pa / 100.0:9.2f}hPa  "
                f"Traw={sample.temperature_c:+6.2f}C  "
                f"T={temperature_c:+6.2f}C  "
                f"rho={density:.4f}kg/m3"
            )
            if configured is not None:
                delta_pct = (density / configured.density_kg_m3 - 1.0) * 100.0
                output += f"  vs configured {delta_pct:+.2f}%"
            print(output)
            index += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sensor.close()


if __name__ == "__main__":
    main()
