import { create } from 'zustand';
import type { UpdateStatusType, UpdateChannel } from '../types/electronUpdate';

interface UpdateStore {
  status: UpdateStatusType;
  channel: UpdateChannel;
  setStatus: (status: UpdateStatusType) => void;
  /** Triggers a feed fetch + version comparison via Electron IPC. No-op in browser. */
  checkForUpdate: () => void;
  /** Starts the full apply pipeline via Electron IPC. Must only be called when
   *  the session is idle (no active shot processing). No-op in browser. */
  applyUpdate: () => void;
  /** Switches the active release channel and persists the preference. No-op in browser. */
  setChannel: (channel: UpdateChannel) => void;
}

export const useUpdateStore = create<UpdateStore>((set) => ({
  status: { type: 'idle' },
  channel: 'stable',
  setStatus: (status) => set({ status }),
  checkForUpdate: () => {
    window.electronUpdate?.checkForUpdate();
  },
  applyUpdate: () => {
    window.electronUpdate?.applyUpdate();
  },
  setChannel: (channel) => {
    window.electronUpdate?.setChannel(channel);
    set({ channel });
  },
}));

/**
 * Call once on app mount (inside Electron only).
 * Subscribes to status pushes from the main process and feeds them into
 * the store.  Also reads the persisted channel preference.
 * Returns an unsubscribe function for cleanup.
 */
export function subscribeToElectronUpdates(): (() => void) | null {
  if (!window.electronUpdate) return null;

  // Restore the persisted channel preference on startup (read-only — no write back).
  window.electronUpdate.getChannel().then((ch) => {
    useUpdateStore.setState({ channel: ch });
  }).catch(() => {});

  return window.electronUpdate.onStatusChange((status) => {
    useUpdateStore.getState().setStatus(status);
  });
}
