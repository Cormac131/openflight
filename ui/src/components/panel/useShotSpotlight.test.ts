import { afterEach, describe, expect, it, vi } from 'vitest';
import { createSpotlightController, shouldOpenSpotlight } from './useShotSpotlight';

describe('shouldOpenSpotlight', () => {
  it('never opens for tiles', () => {
    expect(shouldOpenSpotlight('tiles', true)).toBe(false);
    expect(shouldOpenSpotlight('tiles', false)).toBe(false);
  });

  it('opens timed and sticky only for a new shot', () => {
    expect(shouldOpenSpotlight('timed', true)).toBe(true);
    expect(shouldOpenSpotlight('sticky', true)).toBe(true);
    expect(shouldOpenSpotlight('timed', false)).toBe(false);
    expect(shouldOpenSpotlight('sticky', false)).toBe(false);
  });
});

describe('createSpotlightController', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('timed controller fires hide after durationMs', () => {
    vi.useFakeTimers();
    const hide = vi.fn();
    const { openInitially, start } = createSpotlightController('timed', 5000, true, hide);
    expect(openInitially).toBe(true);
    const stop = start();
    vi.advanceTimersByTime(4999);
    expect(hide).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(hide).toHaveBeenCalledTimes(1);
    stop();
  });

  it('sticky controller never starts a timer', () => {
    vi.useFakeTimers();
    const hide = vi.fn();
    const { start } = createSpotlightController('sticky', 5000, true, hide);
    start();
    vi.advanceTimersByTime(60_000);
    expect(hide).not.toHaveBeenCalled();
  });
});
