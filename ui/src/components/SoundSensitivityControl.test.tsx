import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { SoundSensitivity } from '../types/socket';
import { SoundSensitivityControl } from './DebugPanel';

const noop = () => {};

function sensitivity(overrides: Partial<SoundSensitivity> = {}): SoundSensitivity {
  return {
    enabled: true,
    position: 64,
    max_position: 127,
    default_position: 127,
    sensitivity_percent: 50.4,
    resistance_ohms: 38039,
    preamp_feedback_ohms: 27557,
    series_ohms: 33000,
    simulated: false,
    error: null,
    ...overrides,
  };
}

function render(overrides: Partial<SoundSensitivity> = {}, error: string | null = null) {
  return renderToString(
    <SoundSensitivityControl
      sensitivity={sensitivity(overrides)}
      error={error}
      onUpdate={noop}
    />
  );
}

describe('SoundSensitivityControl', () => {
  it('shows the applied percentage and both resistances', () => {
    const html = render();

    expect(html).toContain('50%');
    expect(html).toContain('38.0 kΩ');
    expect(html).toContain('27.6 kΩ');
  });

  it('drives the slider from the reported position and bounds', () => {
    const html = render({ position: 12, max_position: 127 });

    expect(html).toContain('value="12"');
    expect(html).toContain('max="127"');
    expect(html).toContain('min="0"');
  });

  it('falls back to the default position when the chip cannot be read', () => {
    // A bus error leaves the position genuinely unknown, and the slider still
    // has to render something.
    const html = render({ position: null, sensitivity_percent: null, resistance_ohms: null });

    expect(html).toContain('value="127"');
    expect(html).toContain('--');
  });

  it('explains how to enable the control when no pot is fitted', () => {
    const html = render({ enabled: false, position: null });

    expect(html).toContain('--sound-sensitivity');
  });

  it('warns that a mock wiper is not really being driven', () => {
    expect(render({ simulated: true })).toContain('Mock mode');
  });

  it('surfaces a rejected move', () => {
    expect(render({}, 'i2c write failed')).toContain('i2c write failed');
  });

  it('renders sub-kilohm resistances in ohms', () => {
    const html = render({ position: 0, sensitivity_percent: 0, resistance_ohms: 820 });

    expect(html).toContain('820 Ω');
  });
});
