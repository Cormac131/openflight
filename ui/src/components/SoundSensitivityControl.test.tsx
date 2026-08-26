import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { SoundSensitivity } from '../types/socket';
import { SoundSensitivityControl } from './DebugPanel';

const noop = () => {};

function sensitivity(overrides: Partial<SoundSensitivity> = {}): SoundSensitivity {
  return {
    enabled: true,
    position: 46,
    max_position: 99,
    default_position: 46,
    sensitivity_percent: 46.5,
    resistance_ohms: 46504,
    preamp_feedback_ohms: 31742,
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
      onRecalibrate={noop}
    />
  );
}

describe('SoundSensitivityControl', () => {
  it('shows the applied percentage and both resistances', () => {
    const html = render();

    expect(html).toContain('47%');
    expect(html).toContain('46.5 kΩ');
    expect(html).toContain('31.7 kΩ');
  });

  it('drives the slider from the reported position and bounds', () => {
    const html = render({ position: 12, max_position: 99 });

    expect(html).toContain('value="12"');
    expect(html).toContain('max="99"');
    expect(html).toContain('min="0"');
  });

  it('falls back to the default position when the wiper is uncalibrated', () => {
    // The X9C104 cannot be read back, so a null position is a real state the
    // slider still has to render something for.
    const html = render({ position: null, sensitivity_percent: null, resistance_ohms: null });

    expect(html).toContain('value="46"');
    expect(html).toContain('--');
  });

  it('explains how to enable the control when no pot is fitted', () => {
    const html = render({ enabled: false, position: null });

    expect(html).toContain('--sound-sensitivity');
    expect(html).not.toContain('Recalibrate wiper');
  });

  it('offers the recalibrate action when a pot is fitted', () => {
    expect(render()).toContain('Recalibrate wiper');
  });

  it('warns that a mock wiper is not really being driven', () => {
    expect(render({ simulated: true })).toContain('Mock mode');
  });

  it('surfaces a rejected move', () => {
    expect(render({}, 'wiper line stuck')).toContain('wiper line stuck');
  });

  it('renders sub-kilohm resistances in ohms', () => {
    const html = render({ position: 0, sensitivity_percent: 0, resistance_ohms: 40 });

    expect(html).toContain('40 Ω');
  });
});
