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
      tags: [],
      lastScan: null,
      pendingTag: null,
      blankTag: null,
      writeStage: 'select',
      writeClub: null,
      writeError: null,
      clubScanVersion: 0,
      announcedClub: null,
    });
  });

  it('stores the learned tag list and whether a reader is configured', () => {
    useNfcStore.getState().setClubTags([tag], true);

    expect(useNfcStore.getState().tags).toEqual([tag]);
    expect(useNfcStore.getState().enabled).toBe(true);
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

  describe('the blank-tag write flow', () => {
    const blank = scan({ blank: true, writable: true });

    it('starts on club selection with nothing chosen', () => {
      useNfcStore.getState().setBlankTag(blank);

      expect(useNfcStore.getState().blankTag?.uid).toBe('04A2B1C3');
      expect(useNfcStore.getState().writeStage).toBe('select');
      expect(useNfcStore.getState().writeClub).toBeNull();
    });

    it('moves to confirmation once a club is chosen', () => {
      useNfcStore.getState().setBlankTag(blank);

      useNfcStore.getState().chooseWriteClub('7-iron');

      expect(useNfcStore.getState().writeStage).toBe('confirm');
      expect(useNfcStore.getState().writeClub).toBe('7-iron');
    });

    it('clears the flow once the tag is written', () => {
      useNfcStore.getState().setBlankTag(blank);
      useNfcStore.getState().chooseWriteClub('7-iron');
      useNfcStore.getState().beginWrite();

      useNfcStore.getState().finishWrite({ state: 'written', uid: '04A2B1C3', club: '7-iron' });

      expect(useNfcStore.getState().blankTag).toBeNull();
      expect(useNfcStore.getState().writeStage).toBe('select');
    });

    it('keeps the tag and the chosen club after a failure, so it can be retried', () => {
      useNfcStore.getState().setBlankTag(blank);
      useNfcStore.getState().chooseWriteClub('7-iron');
      useNfcStore.getState().beginWrite();

      useNfcStore.getState().finishWrite({ state: 'failed', error: 'Tag not on the reader' });

      expect(useNfcStore.getState().blankTag?.uid).toBe('04A2B1C3');
      expect(useNfcStore.getState().writeClub).toBe('7-iron');
      expect(useNfcStore.getState().writeStage).toBe('failed');
      expect(useNfcStore.getState().writeError).toBe('Tag not on the reader');
    });

    it('reports a failure even when the server sent no reason', () => {
      useNfcStore.getState().setBlankTag(blank);

      useNfcStore.getState().finishWrite({ state: 'failed' });

      expect(useNfcStore.getState().writeError).toBe('Write failed');
    });

    it('clears a stale error when a retry starts', () => {
      useNfcStore.getState().setBlankTag(blank);
      useNfcStore.getState().finishWrite({ state: 'failed', error: 'Tag not on the reader' });

      useNfcStore.getState().beginWrite();

      expect(useNfcStore.getState().writeStage).toBe('writing');
      expect(useNfcStore.getState().writeError).toBeNull();
    });

    it('abandons the flow on cancel', () => {
      useNfcStore.getState().setBlankTag(blank);
      useNfcStore.getState().chooseWriteClub('7-iron');

      useNfcStore.getState().cancelWrite();

      expect(useNfcStore.getState().blankTag).toBeNull();
      expect(useNfcStore.getState().writeClub).toBeNull();
    });

    it('drops the flow when a known club tag is tapped instead', () => {
      useNfcStore.getState().setBlankTag(blank);

      useNfcStore.getState().recordScan(scan({ uid: '04A2B1C4', club: 'driver', known: true }));

      expect(useNfcStore.getState().blankTag).toBeNull();
    });

    it('does not drop a write already in flight', () => {
      // The write reports its own result; discarding it here would strand the UI.
      useNfcStore.getState().setBlankTag(blank);
      useNfcStore.getState().chooseWriteClub('7-iron');
      useNfcStore.getState().beginWrite();

      useNfcStore.getState().recordScan(scan({ uid: '04A2B1C4', club: 'driver', known: true }));

      expect(useNfcStore.getState().blankTag?.uid).toBe('04A2B1C3');
    });
  });
});
