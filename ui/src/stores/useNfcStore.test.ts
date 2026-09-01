import { beforeEach, describe, expect, it } from 'vitest';
import { useNfcStore } from './useNfcStore';
import type { ClubTag, NfcScan } from '../types/nfc';

function scan(overrides: Partial<NfcScan> = {}): NfcScan {
  return {
    uid: '04A2B1C3',
    uid_display: '04:A2:B1:C3',
    timestamp: 1,
    club: null,
    known: false,
    source: null,
    blank: false,
    writable: false,
    ...overrides,
  };
}

const tag: ClubTag = {
  uid: '04A2B1C3',
  uid_display: '04:A2:B1:C3',
  club: '7-iron',
  learned_at: '2026-01-01T00:00:00+00:00',
  last_seen_at: null,
};

describe('useNfcStore', () => {
  beforeEach(() => {
    useNfcStore.setState({
      enabled: false,
      requested: false,
      error: null,
      tags: [],
      lastScan: null,
      pendingTag: null,
      clubScanVersion: 0,
      announcedClub: null,
    });
  });

  it('stores the learned tag list and whether a reader is running', () => {
    useNfcStore.getState().setClubTags({ tags: [tag], enabled: true, requested: true });

    expect(useNfcStore.getState().tags).toEqual([tag]);
    expect(useNfcStore.getState().enabled).toBe(true);
    expect(useNfcStore.getState().requested).toBe(true);
    expect(useNfcStore.getState().error).toBeNull();
  });

  it('keeps requested tags when the reader failed to start', () => {
    useNfcStore.getState().setClubTags({
      tags: [tag],
      enabled: false,
      requested: true,
      error: 'PN532 not found',
    });

    expect(useNfcStore.getState().tags).toEqual([tag]);
    expect(useNfcStore.getState().enabled).toBe(false);
    expect(useNfcStore.getState().requested).toBe(true);
    expect(useNfcStore.getState().error).toBe('PN532 not found');
  });

  it('queues an unknown tag for the learn prompt', () => {
    useNfcStore.getState().setPendingTag(scan());

    expect(useNfcStore.getState().pendingTag?.uid).toBe('04A2B1C3');
    // Seeing any tag proves a reader is attached, even before club_tags arrives.
    expect(useNfcStore.getState().enabled).toBe(true);
  });

  it('bumps the scan counter only for a tag that already has a club', () => {
    useNfcStore.getState().recordScan(scan({ club: '7-iron', known: true }));
    expect(useNfcStore.getState().clubScanVersion).toBe(1);

    useNfcStore.getState().recordScan(scan({ uid: '04A2B1C4' }));
    expect(useNfcStore.getState().clubScanVersion).toBe(1);
  });

  it('dismisses a learn prompt when a known club tag is tapped instead', () => {
    useNfcStore.getState().setPendingTag(scan());

    useNfcStore.getState().recordScan(scan({ uid: '04A2B1C4', club: 'driver', known: true }));

    expect(useNfcStore.getState().pendingTag).toBeNull();
  });

  it('keeps the learn prompt when another unknown tag is tapped', () => {
    useNfcStore.getState().setPendingTag(scan());

    useNfcStore.getState().recordScan(scan({ uid: '04A2B1C4' }));

    expect(useNfcStore.getState().pendingTag?.uid).toBe('04A2B1C3');
  });

  it('records the last scan for the tag view', () => {
    useNfcStore.getState().recordScan(scan({ club: 'pw', known: true }));

    expect(useNfcStore.getState().lastScan?.club).toBe('pw');
  });

  it('clears the prompt when the user dismisses it', () => {
    useNfcStore.getState().setPendingTag(scan());

    useNfcStore.getState().clearPendingTag();

    expect(useNfcStore.getState().pendingTag).toBeNull();
  });

  it('records the club a known tag selected, for the confirmation toast', () => {
    useNfcStore.getState().recordScan(scan({ club: '7-iron', known: true }));

    expect(useNfcStore.getState().announcedClub).toBe('7-iron');
  });

  it('leaves the announced club alone for an unknown tag', () => {
    useNfcStore.getState().recordScan(scan({ club: 'driver', known: true }));

    useNfcStore.getState().recordScan(scan({ uid: '04A2B1C4' }));

    // The prompt takes over the screen; the stale toast must not contradict it.
    expect(useNfcStore.getState().announcedClub).toBe('driver');
    expect(useNfcStore.getState().clubScanVersion).toBe(1);
  });

  it('advances the counter on a repeat tap so the toast timer restarts', () => {
    useNfcStore.getState().recordScan(scan({ club: 'pw', known: true }));
    useNfcStore.getState().recordScan(scan({ club: 'pw', known: true }));

    expect(useNfcStore.getState().clubScanVersion).toBe(2);
  });
});
