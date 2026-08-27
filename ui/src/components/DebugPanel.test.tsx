import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { AirStatus } from '../types/socket';
import { AirConditionsCard, AirSensorCard } from './DebugPanel';

const air = (overrides: Partial<AirStatus> = {}): AirStatus => ({
  source: 'sensor',
  density_kg_m3: 0.9911,
  pressure_hpa: 834.3,
  temperature_c: 20.0,
  elevation_ft: null,
  normalization_density_kg_m3: 1.225,
  density_delta_pct: -19.09,
  driver_carry_delta_yards: 14.0,
  sensor: {
    enabled: true,
    sensor: 'bmp580',
    i2c_bus: 1,
    i2c_address: '0x47',
    sample_hz: 0.5,
    temperature_offset_c: -4.7,
  },
  reading: {
    applied: true,
    status: 'ok',
    age_s: 2,
    pressure_hpa: 834.3,
    pressure_std_pa: 0.12,
    temperature_c: 21.5,
    raw_temperature_c: 26.2,
    density_kg_m3: 0.9911,
    sample_count: 5,
  },
  ...overrides,
});

describe('AirConditionsCard', () => {
  it('shows density, pressure and temperature', () => {
    const html = renderToString(<AirConditionsCard air={air()} />);
    expect(html).toContain('0.9911');
    expect(html).toContain('834.30');
    expect(html).toContain('20.0');
  });

  it('expresses the density difference as driver carry', () => {
    // The yardage is the only unit an operator can judge by eye.
    const html = renderToString(<AirConditionsCard air={air()} />);
    expect(html).toContain('+14.0');
    expect(html).toContain('-19.09');
  });

  it('signs a denser-than-normalized reading negative', () => {
    const html = renderToString(
      <AirConditionsCard air={air({ density_delta_pct: 5.5, driver_carry_delta_yards: -4.3 })} />
    );
    expect(html).toContain('-4.3');
    expect(html).toContain('+5.50');
  });

  it.each([
    ['sensor', 'Measured', 'air-source--sensor'],
    ['sensor_stale', 'Last known', 'air-source--sensor_stale'],
    ['config', 'Configured', 'air-source--config'],
    ['standard', 'Assumed', 'air-source--standard'],
  ] as const)('labels the %s source as %s', (source, label, className) => {
    const html = renderToString(<AirConditionsCard air={air({ source })} />);
    expect(html).toContain(label);
    expect(html).toContain(className);
  });

  it('warns that a stale reading means the sensor stopped responding', () => {
    const html = renderToString(<AirConditionsCard air={air({ source: 'sensor_stale' })} />);
    expect(html).toContain('stopped responding');
  });

  it('omits elevation when none is configured', () => {
    expect(renderToString(<AirConditionsCard air={air()} />)).not.toContain('Elevation');
  });

  it('shows elevation when one is configured', () => {
    const html = renderToString(<AirConditionsCard air={air({ elevation_ft: 5280 })} />);
    expect(html).toContain('5280');
  });
});

describe('AirSensorCard', () => {
  it('shows the device, address and bus', () => {
    const html = renderToString(<AirSensorCard air={air()} />);
    expect(html).toContain('bmp580');
    expect(html).toContain('0x47');
    expect(html).toContain('i2c-1');
  });

  it('shows the raw die temperature alongside the corrected one', () => {
    // Self-heating is the sensor's main failure mode, so the uncorrected
    // reading has to stay visible rather than being absorbed into the offset.
    const html = renderToString(<AirSensorCard air={air()} />);
    expect(html).toContain('26.20');
    expect(html).toContain('21.50');
    expect(html).toContain('-4.70');
  });

  it('reports reading status and age', () => {
    const html = renderToString(<AirSensorCard air={air()} />);
    expect(html).toContain('ok');
    expect(html).toContain('2s ago');
  });

  it('flags an error status', () => {
    const html = renderToString(
      <AirSensorCard air={air({ reading: { applied: false, status: 'stale', age_s: 900 } })} />
    );
    expect(html).toContain('stale');
    expect(html).toContain('system-status__value--error');
  });

  it('warns when no temperature offset has been calibrated', () => {
    const html = renderToString(<AirSensorCard air={air({ sensor: { ...air().sensor, temperature_offset_c: 0 } })} />);
    expect(html).toContain('No temperature offset set');
    expect(html).toContain('debug-panel__hint--warn');
  });

  it('does not warn when an offset is calibrated', () => {
    expect(renderToString(<AirSensorCard air={air()} />)).not.toContain('No temperature offset set');
  });

  it('explains how to enable a barometer when none is fitted', () => {
    const html = renderToString(<AirSensorCard air={air({ sensor: { enabled: false } })} />);
    expect(html).toContain('--barometer');
  });

  it('surfaces an initialization error when the sensor was requested but failed', () => {
    const html = renderToString(
      <AirSensorCard air={air({ sensor: { enabled: false, error: 'CHIP_ID expected one of 0x50, 0x51' } })} />
    );
    expect(html).toContain('CHIP_ID');
  });
});
