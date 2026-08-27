import { create } from 'zustand';
import type { ClubTag, NfcScan } from '../types/nfc';

interface NfcState {
  /** True once the server reports an NFC reader is configured. */
  enabled: boolean;
  tags: ClubTag[];
  lastScan: NfcScan | null;
  /** Tag the reader saw that has no club yet; drives the learn overlay. */
  pendingTag: NfcScan | null;
  /**
   * Bumped every time a *known* tag selects a club. App watches this to close
   * the club picker, which a plain club change must not do -- the picker opens
   * deliberately on startup and would otherwise vanish on the connect snapshot.
   */
  clubScanVersion: number;
  setClubTags: (tags: ClubTag[], enabled: boolean) => void;
  recordScan: (scan: NfcScan) => void;
  setPendingTag: (scan: NfcScan) => void;
  clearPendingTag: () => void;
}

export const useNfcStore = create<NfcState>((set) => ({
  enabled: false,
  tags: [],
  lastScan: null,
  pendingTag: null,
  clubScanVersion: 0,
  setClubTags: (tags, enabled) => set({ tags, enabled }),
  recordScan: (scan) =>
    set((state) => ({
      lastScan: scan,
      enabled: true,
      clubScanVersion: scan.known ? state.clubScanVersion + 1 : state.clubScanVersion,
      // A recognized tap resolves whatever learn prompt was open: the user
      // walked away from the unknown tag and grabbed a club that is already set up.
      pendingTag: scan.known ? null : state.pendingTag,
    })),
  setPendingTag: (scan) => set({ pendingTag: scan, enabled: true }),
  clearPendingTag: () => set({ pendingTag: null }),
}));
