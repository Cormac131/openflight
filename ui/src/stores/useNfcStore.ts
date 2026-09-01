import { create } from 'zustand';
import type { ClubTag, ClubTagsPayload, NfcScan } from '../types/nfc';

interface NfcState {
  /** True when the PN532 opened and the reader thread is running. */
  enabled: boolean;
  /** True when `--nfc` was requested, even if the reader failed to start. */
  requested: boolean;
  /** Reader init failure to show in the menu when the PN532 is down. */
  error: string | null;
  tags: ClubTag[];
  lastScan: NfcScan | null;
  /** Tag the reader saw that has no club yet; drives the learn overlay. */
  pendingTag: NfcScan | null;
  /**
   * Bumped every time a *known* tag selects a club. App watches this to close
   * the club picker and to raise the confirmation toast. A plain club change
   * must do neither -- the picker opens deliberately on startup and would
   * otherwise vanish on the connect snapshot.
   */
  clubScanVersion: number;
  /** Club the last tag tap selected, shown by the confirmation toast. */
  announcedClub: string | null;
  setClubTags: (payload: ClubTagsPayload) => void;
  recordScan: (scan: NfcScan) => void;
  setPendingTag: (scan: NfcScan) => void;
  clearPendingTag: () => void;
}

export const useNfcStore = create<NfcState>((set) => ({
  enabled: false,
  requested: false,
  error: null,
  tags: [],
  lastScan: null,
  pendingTag: null,
  clubScanVersion: 0,
  announcedClub: null,
  setClubTags: (payload) =>
    set({
      tags: payload.tags,
      enabled: payload.enabled,
      requested: payload.requested ?? payload.enabled,
      error: payload.error ?? null,
    }),
  recordScan: (scan) =>
    set((state) => ({
      lastScan: scan,
      enabled: true,
      requested: true,
      clubScanVersion: scan.known ? state.clubScanVersion + 1 : state.clubScanVersion,
      announcedClub: scan.known ? scan.club : state.announcedClub,
      pendingTag: scan.known ? null : state.pendingTag,
    })),
  setPendingTag: (scan) => set({ pendingTag: scan, enabled: true, requested: true }),
  clearPendingTag: () => set({ pendingTag: null }),
}));
