#!/usr/bin/env python3
"""
Average a settled BMP580 and print its recommended temperature-offset flag.

The BMP580's pressure channel needs no calibration for carry — 1 hPa of error
is about 0.3 yd on a driver, well inside the sensor's own accuracy. Its
temperature channel does: the die self-heats inside a warm enclosure, and 3 C
of error is roughly 1% air density, about a yard of driver carry. That is the
entire benefit of fitting the sensor, so it is worth ten minutes to remove.

Let the rig reach its normal running temperature first — calibrating a cold
sensor bakes in an offset that disappears once the Pi warms up.
"""

import argparse
import statistics
import time

from openflight.air_density import air_density
from openflight.barometer import BMP580


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", type=int, default=1)
    parser.add_argument("--address", type=lambda value: int(value, 0), default=0x47)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument(
        "--reference-temp-c",
        type=float,
        required=True,
        help="True ambient temperature in C from a separate thermometer",
    )
    args = parser.parse_args()
    if args.samples < 2:
        parser.error("--samples must be at least 2")

    sensor = BMP580(bus_number=args.bus, address=args.address)
    sensor.initialize()
    temperatures = []
    pressures = []
    try:
        print(
            f"Collecting {args.samples} samples at {args.interval:g}s intervals. "
            "The rig should already be at its normal running temperature."
        )
        for _ in range(args.samples):
            sample = sensor.read()
            temperatures.append(sample.temperature_c)
            pressures.append(sample.pressure_pa)
            time.sleep(args.interval)
    finally:
        sensor.close()

    raw_temp = statistics.mean(temperatures)
    temp_std = statistics.pstdev(temperatures)
    pressure = statistics.mean(pressures)
    offset = args.reference_temp_c - raw_temp

    print(f"Raw temperature mean: {raw_temp:+.3f}C (std dev {temp_std:.3f}C)")
    print(f"Reference temperature: {args.reference_temp_c:+.3f}C")
    print(f"Mean pressure: {pressure / 100.0:.2f} hPa")

    uncorrected = air_density(pressure, raw_temp)
    corrected = air_density(pressure, raw_temp + offset)
    error_pct = abs(uncorrected / corrected - 1.0) * 100.0
    print(
        f"Uncorrected density {uncorrected:.4f} vs corrected {corrected:.4f} kg/m3 "
        f"({error_pct:.2f}% error, about {error_pct * 0.75:.1f} yd of driver carry)"
    )
    print(f"Recommended flag: --barometer-temp-offset-c {offset:+.2f}")

    if temp_std > 0.5:
        print(
            "WARNING: temperature is still drifting. Let the rig settle longer "
            "before trusting this offset."
        )


if __name__ == "__main__":
    main()
