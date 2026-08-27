import { beforeEach, describe, expect, it } from 'vitest';
import { useUpdateStore } from './useUpdateStore';

describe('useUpdateStore', () => {
  beforeEach(() => {
    useUpdateStore.setState({ pendingUpdate: null, dismissedTag: null });
  });

  it('starts with no pending update and nothing dismissed', () => {
    const state = useUpdateStore.getState();
    expect(state.pendingUpdate).toBeNull();
    expect(state.dismissedTag).toBeNull();
  });

  it('setPendingUpdate replaces the current pending release', () => {
    useUpdateStore.getState().setPendingUpdate({ tag: 'v0.3.0', notes: 'notes' });
    expect(useUpdateStore.getState().pendingUpdate).toEqual({ tag: 'v0.3.0', notes: 'notes' });

    useUpdateStore.getState().setPendingUpdate(null);
    expect(useUpdateStore.getState().pendingUpdate).toBeNull();
  });

  it('dismissForSession records the tag; clearDismissed resets it', () => {
    useUpdateStore.getState().dismissForSession('v0.3.0');
    expect(useUpdateStore.getState().dismissedTag).toBe('v0.3.0');

    useUpdateStore.getState().clearDismissed();
    expect(useUpdateStore.getState().dismissedTag).toBeNull();
  });
});
