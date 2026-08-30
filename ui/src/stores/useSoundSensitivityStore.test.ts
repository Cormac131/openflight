import { beforeEach, describe, expect, it } from 'vitest';

import { useDebugStore } from './useDebugStore';
import type { SoundSensitivity } from '../types/socket';

const applied: SoundSensitivity = {
  enabled: true,
  position: 100,
  max_position: 127,
  default_position: 127,
  sensitivity_percent: 78.7,
  resistance_ohms: 40874,
  preamp_feedback_ohms: 29013,
  series_ohms: 33000,
  simulated: false,
  auto_available: false,
  auto_enabled: false,
  last_peak: null,
  last_decision: null,
  live_envelope: null,
  target_low: null,
  target_high: null,
  error: null,
};

describe('useDebugStore sound sensitivity', () => {
  beforeEach(() => {
    useDebugStore.setState({ soundSensitivityError: null });
  });

  it('starts assuming no digital pot is fitted', () => {
    expect(useDebugStore.getState().soundSensitivity.enabled).toBe(false);
    expect(useDebugStore.getState().soundSensitivity.position).toBeNull();
  });

  it('stores an applied state from the server', () => {
    useDebugStore.getState().setSoundSensitivity(applied);

    expect(useDebugStore.getState().soundSensitivity.position).toBe(100);
  });

  it('clears a stale error once a fresh state arrives', () => {
    useDebugStore.getState().setSoundSensitivityError('i2c write failed');

    useDebugStore.getState().setSoundSensitivity(applied);

    expect(useDebugStore.getState().soundSensitivityError).toBeNull();
  });

  it('adopts an error carried on the state itself', () => {
    useDebugStore.getState().setSoundSensitivity({
      ...applied,
      error: 'Sensitivity applied but not saved: disk full',
    });

    expect(useDebugStore.getState().soundSensitivityError).toContain('not saved');
  });
});
