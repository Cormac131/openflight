import { beforeEach, describe, expect, it } from 'vitest';

import { useDebugStore } from './useDebugStore';
import type { SoundSensitivity } from '../types/socket';

const applied: SoundSensitivity = {
  enabled: true,
  position: 60,
  max_position: 99,
  default_position: 46,
  sensitivity_percent: 60.6,
  resistance_ohms: 60646,
  preamp_feedback_ohms: 37752,
  simulated: false,
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

    expect(useDebugStore.getState().soundSensitivity.position).toBe(60);
  });

  it('clears a stale error once a fresh state arrives', () => {
    useDebugStore.getState().setSoundSensitivityError('wiper line stuck');

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
