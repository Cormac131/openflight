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
      pendingAssign: null,
      pendingForgetUid: null,
      assignError: null,
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

  it('does not open the tag prompt for a known tag', () => {
    useNfcStore.getState().setPendingTag(scan());

    useNfcStore.getState().recordScan(scan({ uid: '04A2B1C4', club: 'driver', known: true }));

    expect(useNfcStore.getState().pendingTag).toBeNull();
    expect(useNfcStore.getState().lastScan).toEqual(
      expect.objectContaining({ uid: '04A2B1C4', club: 'driver', known: true })
    );
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

  it('keeps the learn prompt until the server persists the mapping', () => {
    useNfcStore.getState().setPendingTag(scan());
    useNfcStore.getState().requestAssign('04A2B1C3', '7-iron');

    useNfcStore.getState().setClubTags({ tags: [], enabled: true, requested: true });

    expect(useNfcStore.getState().pendingTag?.uid).toBe('04A2B1C3');
    expect(useNfcStore.getState().pendingAssign).toEqual({ uid: '04A2B1C3', club: '7-iron' });
  });

  it('closes the learn prompt only after the mapping appears in club_tags', () => {
    useNfcStore.getState().setPendingTag(scan());
    useNfcStore.getState().requestAssign('04A2B1C3', '7-iron');

    useNfcStore.getState().setClubTags({ tags: [tag], enabled: true, requested: true });

    expect(useNfcStore.getState().pendingTag).toBeNull();
    expect(useNfcStore.getState().pendingAssign).toBeNull();
    expect(useNfcStore.getState().assignError).toBeNull();
  });

  it('surfaces a rejected assignment on the same prompt', () => {
    useNfcStore.getState().setPendingTag(scan());
    useNfcStore.getState().requestAssign('04A2B1C3', '7-iron');

    useNfcStore.getState().setClubTagError('Could not save club tags: read-only filesystem', '04A2B1C3');

    expect(useNfcStore.getState().pendingTag?.uid).toBe('04A2B1C3');
    expect(useNfcStore.getState().pendingAssign).toBeNull();
    expect(useNfcStore.getState().assignError).toBe('Could not save club tags: read-only filesystem');
  });

  it('does not mark a tag forgotten until club_tags drops it', () => {
    useNfcStore.getState().setPendingTag(scan({ known: true, club: '7-iron' }));
    useNfcStore.getState().requestForget('04A2B1C3');

    useNfcStore.getState().setClubTags({ tags: [tag], enabled: true, requested: true });

    expect(useNfcStore.getState().pendingTag).toEqual(
      expect.objectContaining({ uid: '04A2B1C3', known: true, club: '7-iron' })
    );
  });

  it('turns a forgotten tag back into a learn prompt after the registry drops it', () => {
    useNfcStore.getState().setPendingTag(scan({ known: true, club: '7-iron' }));
    useNfcStore.getState().requestForget('04A2B1C3');

    useNfcStore.getState().setClubTags({ tags: [], enabled: true, requested: true });

    expect(useNfcStore.getState().pendingTag).toEqual(
      expect.objectContaining({ uid: '04A2B1C3', known: false, club: null })
    );
    expect(useNfcStore.getState().pendingForgetUid).toBeNull();
  });

  it('keeps a known tag on screen when forget fails to persist', () => {
    useNfcStore.getState().setPendingTag(scan({ known: true, club: '7-iron' }));
    useNfcStore.getState().requestForget('04A2B1C3');

    useNfcStore.getState().setClubTagError('Could not save club tags: disk full', '04A2B1C3');

    expect(useNfcStore.getState().pendingTag).toEqual(
      expect.objectContaining({ known: true, club: '7-iron' })
    );
    expect(useNfcStore.getState().assignError).toBe('Could not save club tags: disk full');
  });
});
