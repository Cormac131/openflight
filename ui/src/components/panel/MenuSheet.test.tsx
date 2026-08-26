import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import { MenuSheet } from './MenuSheet';
import { useSystemStore } from '../../stores/useSystemStore';
import { useLiveViewStore } from '../../stores/useLiveViewStore';
import type { PowerStatus } from '../../types/power';

function renderMenu() {
  return renderToString(<MenuSheet onClose={() => {}} />);
}

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

describe('MenuSheet battery', () => {
  it('does not show battery in the menu, even when telemetry is present', () => {
    const powerStatus: PowerStatus = {
      available: true,
      provider: 'geekworm',
      state: 'on_battery',
      battery_percent: 64.2,
      battery_voltage_v: 3.81,
      external_power: false,
      updated_at: '2026-08-20T12:00:00+00:00',
      error: null,
    };
    useSystemStore.setState({ powerStatus });

    const html = renderMenu();

    expect(html).not.toContain('menu-sheet__status-label">Battery');
    expect(html).not.toContain('power-status');
    expect(html).not.toContain('64%');

    useSystemStore.setState({ powerStatus: null });
  });
});

describe('MenuSheet live view', () => {
  it('offers live view modes and hides duration unless timed', () => {
    useLiveViewStore.setState({ mode: 'tiles', durationMs: 10000 });
    const html = renderMenu();

    expect(html).toContain('menu-sheet__section-title">Live view');
    expect(html).toContain('>Tiles<');
    expect(html).toContain('>Timed<');
    expect(html).toContain('>Hold<');
    expect(html).not.toContain('>5s<');
  });

  it('does not show system, ball detection, or simulators', () => {
    const html = renderMenu();

    expect(html).not.toContain('menu-sheet__section-title">System');
    expect(html).not.toContain('Ball detection');
    expect(html).not.toContain('Simulators');
  });

  it('shows duration chips when timed is selected', () => {
    useLiveViewStore.setState({ mode: 'timed', durationMs: 10000 });
    const html = renderMenu();

    expect(html).toContain('>5s<');
    expect(html).toContain('>10s<');
    expect(html).toContain('>15s<');
  });
});

describe('MenuSheet shutdown', () => {
  it('does not offer shut down in the sheet', () => {
    const html = renderMenu();
    expect(html).not.toContain('menu-sheet__shutdown');
    expect(html).not.toContain('Shut down');
  });
});
