# Air Density and Carry

Carry distance depends on air density. Thinner air means less drag and less
Magnus lift, and for a golf ball drag dominates, so the same launch flies
further as density drops.

Until this was modelled, every OpenFlight carry was computed at a hardcoded
1.225 kg/m³ — dry air at sea level, 15 °C. For a rig at altitude that is a
large, permanent, one-directional error.

## How much it matters

Measured by running OpenFlight's own RK4 simulator (`src/openflight/ballistics.py`)
across realistic conditions. Deltas are relative to the 1.225 kg/m³ baseline.

| Scenario | ρ (kg/m³) | Driver | 7-iron | PW |
|---|---|---|---|---|
| ISA sea level, 15 °C (the old fixed default) | 1.225 | 260.5 | 181.4 | 142.6 |
| Sea level, 0 °C winter | 1.292 | −4.3 | −3.6 | −3.0 |
| Sea level, 35 °C summer | 1.145 | +5.0 | +4.2 | +3.6 |
| Sea level, deep low 980 hPa, 15 °C | 1.185 | +2.5 | +2.1 | +1.8 |
| Sea level, strong high 1035 hPa, 15 °C | 1.251 | −1.7 | −1.4 | −1.2 |
| 500 ft, 15 °C | 1.203 | +1.4 | +1.2 | +1.0 |
| 1000 ft, 15 °C | 1.181 | +2.7 | +2.3 | +2.0 |
| 3000 ft, 20 °C | 1.079 | +9.0 | +7.7 | +6.6 |
| 5280 ft (Denver), 20 °C | 0.991 | **+14.0** | +12.1 | +10.6 |

Broken down by which input drives the error:

| Term | Worth (driver carry) | Needs |
|---|---|---|
| Elevation | up to 14 yd | A number you type once. It does not change. |
| Temperature | ~9 yd across a season | A reading, or a number you update. |
| Barometric weather | ±1 to 2.5 yd | A barometer — this is the only term a sensor uniquely buys. |
| Humidity | 0.7 yd at 20 °C saturated, 1.4 yd at 35 °C | A humidity sensor. Safe to omit in temperate air. |

## Two carries, not one

Shots now carry two numbers:

- **`carry_spin_adjusted`** — the headline. Normalized to a fixed density so
  numbers stay comparable across sessions and against published launch-monitor
  data. Unchanged by your site's conditions. This is what the UI shows large,
  and what simulator connectors receive.
- **`carry_actual_yards`** — what the ball did in the air you are actually
  standing in. Emitted only when your configured or measured air differs from
  the normalization air by more than 0.1%; otherwise `null`, so an
  unconfigured install produces exactly the payload it always did.

Both are written to the session log, along with `air_density_kg_m3` and
`air_conditions_source` (`standard`, `config`, or `sensor`) so a later reader
can always tell a measured density from an assumed one.

## Configuration

All flags are optional. With none of them set, behaviour is identical to
before air density was modelled.

```bash
scripts/start-kiosk.sh --elevation-ft 5280 --air-temp-c 20
```

| Flag | Meaning |
|---|---|
| `--elevation-ft` | Site elevation above sea level. The single largest term. |
| `--air-temp-c` | Ambient temperature. Defaults to the standard-atmosphere value for the elevation, which is a weak assumption. |
| `--sea-level-pressure-hpa` | Current QNH from a local forecast. Defaults to ISA 1013.25. |
| `--relative-humidity-pct` | 0–100. Safe to omit in temperate air. |
| `--carry-normalization-density` | Density the headline carry is normalized to. Defaults to ISA 1.225. Use `1.184` to compare against TrackMan "Flat" figures. |

Implausible values are rejected at startup rather than silently shifting every
carry — a mistyped elevation is worth 14 yd, which is far too large to let
through quietly.

### Matching TrackMan "Flat"

`scripts/analysis/validate_ballistics.py` validates against TrackMan "Flat"
carry, which is normalized to no wind, 0 ft, and 77 °F — 1.184 kg/m³, not
1.225. If you are comparing OpenFlight numbers to TrackMan figures, set
`--carry-normalization-density 1.184` so both sides use the same reference.

## On barometers

A barometer measures **station pressure**, which with temperature gives density
directly. That is the right measurement and the reason to fit one.

What a barometer does *not* measure is altitude. Breakout drivers (BMP280,
BMP390, BMP580) expose an `altitude` property, but it is computed from the
measured pressure by assuming a sea-level reference — the drivers hardcode ISA
1013.25 hPa. A sensor that never moves therefore reports an altitude that
wanders by over 1500 ft as weather systems pass:

| Actual sea-level pressure | Reported "altitude" | True ρ at 15 °C |
|---|---|---|
| 980 hPa | +920 ft | 1.185 |
| 995 hPa | +502 ft | 1.203 |
| 1013.25 hPa | 0 ft | 1.225 |
| 1025 hPa | −319 ft | 1.239 |
| 1035 hPa | −589 ft | 1.251 |

The density column is exact at every row. Routing pressure → altitude → ISA →
back to pressure reintroduces precisely the error the sensor was fitted to
remove. Use `AirConditions.from_sensor()` with the raw pressure and ignore the
driver's altitude.

Two practical notes if you fit one:

- **Precision is not the constraint.** The BMP580's ±0.06 hPa relative accuracy
  works out to 0.006% density, about 0.002 yd of carry. A ±1 hPa sensor gives
  0.3 yd. Any barometer is far more precise than this application needs.
- **Mounting is the constraint.** A sensor die sharing an enclosure with a
  Raspberry Pi reads several degrees warm from self-heating, and 3 °C of
  temperature error is about 1% density — comparable to the entire benefit of
  measuring pressure at all. Mount it in moving ambient air, outside the warm
  enclosure, or calibrate the offset.

## Implementation notes

`src/openflight/air_density.py` holds the atmosphere model — gas law, ISA
barometric formula, Buck saturation vapour pressure — and the `AirConditions`
value type with its three constructors (`standard`, `from_elevation`,
`from_sensor`). It has no golf-specific coupling.

Two carry paths exist and both are density-aware:

- The **ballistic path** (`simulate`) takes density directly, so it simply runs
  twice — once at the normalization density, once at the site's.
- The **table path** (`estimate_carry_with_spin`, used when ballistics is
  disabled or no launch angle was measured) has no launch angle to simulate.
  Rather than fit a second density model that could drift out of step with the
  physics, it scales by `density_carry_ratio()`, which runs the real simulator
  twice on a club-typical flight and keeps only the ratio. Ratios tolerate a
  guessed launch angle far better than absolute carries do; this tracks a
  directly-simulated carry to within ~1 yd across densities from 0.95 to 1.30
  kg/m³. Without this, a Denver user would see a 14 yd altitude gain with
  ballistics on and nothing at all with it off.
