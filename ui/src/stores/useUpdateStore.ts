import { create } from 'zustand';

export interface PendingUpdate {
  tag: string;
  notes: string;
}

interface UpdateState {
  pendingUpdate: PendingUpdate | null;
  // "Next restart" dismisses the dialog for this browser session only (not
  // persisted server-side, unlike "Never") — cleared on reconnect so a fresh
  // page load or dropped connection prompts again.
  dismissedTag: string | null;
  setPendingUpdate: (update: PendingUpdate | null) => void;
  dismissForSession: (tag: string) => void;
  clearDismissed: () => void;
}

export const useUpdateStore = create<UpdateState>((set) => ({
  pendingUpdate: null,
  dismissedTag: null,
  setPendingUpdate: (pendingUpdate) => set({ pendingUpdate }),
  dismissForSession: (tag) => set({ dismissedTag: tag }),
  clearDismissed: () => set({ dismissedTag: null }),
}));
