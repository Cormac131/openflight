import { create } from 'zustand';
import type { ClubTag, ClubTagWrite, NfcScan, WriteStage } from '../types/nfc';

interface NfcState {
  /** True once the server reports an NFC reader is configured. */
  enabled: boolean;
  tags: ClubTag[];
  lastScan: NfcScan | null;
  /** Tag the reader saw that has no club yet; drives the learn overlay. */
  pendingTag: NfcScan | null;
  /**
   * A blank tag the reader can write, and how far the write flow has got.
   * Separate from `pendingTag`: that one only records the club on this rig,
   * while this one puts it on the tag, so the two must not share an overlay.
   */
  blankTag: NfcScan | null;
  writeStage: WriteStage;
  /** Club chosen for the blank tag, pending confirmation. */
  writeClub: string | null;
  writeError: string | null;
  /**
   * Bumped every time a *known* tag selects a club. App watches this to close
   * the club picker and to raise the confirmation toast. A plain club change
   * must do neither -- the picker opens deliberately on startup and would
   * otherwise vanish on the connect snapshot.
   */
  clubScanVersion: number;
  /** Club the last tag tap selected, shown by the confirmation toast. */
  announcedClub: string | null;
  setClubTags: (tags: ClubTag[], enabled: boolean) => void;
  recordScan: (scan: NfcScan) => void;
  setPendingTag: (scan: NfcScan) => void;
  clearPendingTag: () => void;
  setBlankTag: (scan: NfcScan) => void;
  chooseWriteClub: (clubId: string) => void;
  beginWrite: () => void;
  finishWrite: (result: ClubTagWrite) => void;
  cancelWrite: () => void;
}

export const useNfcStore = create<NfcState>((set) => ({
  enabled: false,
  tags: [],
  lastScan: null,
  pendingTag: null,
  blankTag: null,
  writeStage: 'select',
  writeClub: null,
  writeError: null,
  clubScanVersion: 0,
  announcedClub: null,
  setClubTags: (tags, enabled) => set({ tags, enabled }),
  recordScan: (scan) =>
    set((state) => ({
      lastScan: scan,
      enabled: true,
      clubScanVersion: scan.known ? state.clubScanVersion + 1 : state.clubScanVersion,
      announcedClub: scan.known ? scan.club : state.announcedClub,
      // A recognized tap resolves whatever prompt was open: the user walked
      // away from that tag and grabbed a club that is already set up. A write
      // in flight is left alone -- it is about to report its own result.
      pendingTag: scan.known ? null : state.pendingTag,
      blankTag: scan.known && state.writeStage !== 'writing' ? null : state.blankTag,
    })),
  setPendingTag: (scan) => set({ pendingTag: scan, enabled: true }),
  clearPendingTag: () => set({ pendingTag: null }),
  setBlankTag: (scan) =>
    set({ blankTag: scan, writeStage: 'select', writeClub: null, writeError: null, enabled: true }),
  chooseWriteClub: (clubId) => set({ writeClub: clubId, writeStage: 'confirm' }),
  beginWrite: () => set({ writeStage: 'writing', writeError: null }),
  finishWrite: (result) =>
    set((state) =>
      result.state === 'written'
        ? { blankTag: null, writeStage: 'select', writeClub: null, writeError: null }
        : // Stay on the tag so the player can hold it down and retry, rather
          // than losing the club they already chose.
          { writeStage: 'failed', writeError: result.error ?? 'Write failed', blankTag: state.blankTag }
    ),
  cancelWrite: () => set({ blankTag: null, writeStage: 'select', writeClub: null, writeError: null }),
}));
