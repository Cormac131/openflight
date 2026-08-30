import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { startEnvelopePoll } from './envelopePoll';

describe('startEnvelopePoll', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('refreshes about ten times a second', () => {
    const refresh = vi.fn();
    const stop = startEnvelopePoll(refresh);

    vi.advanceTimersByTime(1000);
    stop();

    expect(refresh).toHaveBeenCalledTimes(10);
  });

  it('stops after teardown', () => {
    const refresh = vi.fn();
    const stop = startEnvelopePoll(refresh);

    stop();
    vi.advanceTimersByTime(500);

    expect(refresh).not.toHaveBeenCalled();
  });
});
