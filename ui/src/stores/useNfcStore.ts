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
  /** Latest scanned tag driving the learn or re-teach overlay. */
  pendingTag: NfcScan | null;
  /** Club the operator just picked; cleared when club_tags confirms it. */
  pendingAssign: { uid: string; club: string } | null;
  /** UID the operator asked to forget; cleared when club_tags drops it. */
  pendingForgetUid: string | null;
  /** Last assign/forget failure to show on the learn prompt. */
  assignError: string | null;
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
  requestAssign: (uid: string, club: string) => void;
  requestForget: (uid: string) => void;
  setClubTagError: (error: string, uid?: string) => void;
}

export const useNfcStore = create<NfcState>((set) => ({
  enabled: false,
  requested: false,
  error: null,
  tags: [],
  lastScan: null,
  pendingTag: null,
  pendingAssign: null,
  pendingForgetUid: null,
  assignError: null,
  clubScanVersion: 0,
  announcedClub: null,
  setClubTags: (payload) =>
    set((state) => {
      const next = {
        tags: payload.tags,
        enabled: payload.enabled,
        requested: payload.requested ?? payload.enabled,
        error: payload.error ?? null,
      };
      const assign = state.pendingAssign;
      if (assign && payload.tags.some((tag) => tag.uid === assign.uid && tag.club === assign.club)) {
        return { ...next, pendingTag: null, pendingAssign: null, assignError: null };
      }
      const forgetUid = state.pendingForgetUid;
      if (forgetUid && !payload.tags.some((tag) => tag.uid === forgetUid)) {
        const pending = state.pendingTag;
        return {
          ...next,
          pendingForgetUid: null,
          assignError: null,
          pendingTag:
            pending && pending.uid === forgetUid
              ? { ...pending, known: false, club: null, source: null }
              : pending,
        };
      }
      return next;
    }),
  recordScan: (scan) =>
    set((state) => ({
      lastScan: scan,
      enabled: true,
      requested: true,
      assignError: null,
      clubScanVersion: scan.known ? state.clubScanVersion + 1 : state.clubScanVersion,
      announcedClub: scan.known ? scan.club : state.announcedClub,
      pendingTag: scan.known ? scan : state.pendingTag,
    })),
  setPendingTag: (scan) =>
    set({
      pendingTag: scan,
      pendingAssign: null,
      pendingForgetUid: null,
      assignError: null,
      enabled: true,
      requested: true,
    }),
  clearPendingTag: () =>
    set({ pendingTag: null, pendingAssign: null, pendingForgetUid: null, assignError: null }),
  requestAssign: (uid, club) => set({ pendingAssign: { uid, club }, pendingForgetUid: null, assignError: null }),
  requestForget: (uid) => set({ pendingForgetUid: uid, pendingAssign: null, assignError: null }),
  setClubTagError: (error, uid) =>
    set((state) => {
      if (uid && state.pendingTag && state.pendingTag.uid !== uid) {
        return {};
      }
      return { assignError: error, pendingAssign: null, pendingForgetUid: null };
    }),
}));
