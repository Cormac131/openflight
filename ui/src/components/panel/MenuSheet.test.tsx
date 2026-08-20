import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import type { PowerStatus } from '../../types/power';
import { MenuSheet } from './MenuSheet';

function renderMenu(powerStatus?: PowerStatus | null) {
  return renderToString(<MenuSheet onClose={() => {}} onShutdown={() => {}} powerStatus={powerStatus} />);
}

const batteryStatus = (overrides: Partial<PowerStatus> = {}): PowerStatus => ({
  available: true,
  provider: 'geekworm',
  state: 'on_battery',
  battery_percent: 64.2,
  battery_voltage_v: 3.81,
  external_power: false,
  updated_at: '2026-08-20T12:00:00+00:00',
  error: null,
  ...overrides,
});

describe('MenuSheet players', () => {
  it('does not manage players in the menu', () => {
    const html = renderMenu();

    expect(html).not.toContain('menu-sheet__section-title">Player');
    expect(html).not.toContain('Add player');
    expect(html).not.toContain('menu-sheet__input');
  });
});

describe('MenuSheet language', () => {
  it('offers a language dropdown with the shipped locales', () => {
    const html = renderMenu();

    expect(html).toContain('menu-sheet__section-title">Language');
    expect(html).toContain('aria-label="Language"');
    expect(html).toContain('>English</option>');
    expect(html).toContain('>Español</option>');
    expect(html).toContain('>Français</option>');
    expect(html).toContain('>Português</option>');
  });
});

describe('MenuSheet battery row', () => {
  it('hides the battery row when no battery is present', () => {
    const html = renderMenu();

    expect(html).not.toContain('menu-sheet__status-label">Battery');
  });

  it('shows the battery row when telemetry is available', () => {
    const html = renderMenu(batteryStatus());

    expect(html).toContain('menu-sheet__status-label">Battery');
    expect(html).toContain('On battery, 64% battery');
  });
});
