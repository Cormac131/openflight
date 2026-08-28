import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ClubTagWriteFlow } from './ClubTagWriteFlow';
import type { NfcScan, WriteStage } from '../../types/nfc';

const blank: NfcScan = {
  uid: '04A2B1C3',
  uid_display: '04:A2:B1:C3',
  timestamp: 1,
  club: null,
  known: false,
  source: null,
  blank: true,
  writable: true,
};

function render(stage: WriteStage, club: string | null = '7-iron', error: string | null = null) {
  return renderToString(
    <ClubTagWriteFlow
      scan={blank}
      stage={stage}
      club={club}
      error={error}
      onChoose={() => {}}
      onConfirm={() => {}}
      onCancel={() => {}}
    />
  );
}

describe('ClubTagWriteFlow', () => {
  describe('choosing a club', () => {
    it('names the tag as blank rather than unrecognized', () => {
      const html = render('select');

      expect(html).toContain('aria-label="Blank tag"');
      expect(html).toContain('04:A2:B1:C3');
    });

    it('preselects no club, since the choice is written to the tag', () => {
      expect(render('select')).not.toContain('picker-overlay__option--selected');
    });

    it('can be abandoned without writing anything', () => {
      expect(render('select')).toContain('aria-label="Close Blank tag"');
    });
  });

  describe('confirming', () => {
    it('asks before committing, naming the club and the tag', () => {
      const html = render('confirm');

      expect(html).toContain('Write 7 Iron to this tag?');
      expect(html).toContain('04:A2:B1:C3');
    });

    it('offers a way out as well as a way forward', () => {
      const html = render('confirm');

      expect(html).toContain('Write tag');
      expect(html).toContain('Cancel');
    });

    it('names the club in full rather than by its id', () => {
      expect(render('confirm', 'pw')).toContain('Pitching Wedge');
    });
  });

  describe('writing', () => {
    it('tells the player to keep the club on the reader', () => {
      expect(render('writing')).toContain('Hold the club on the reader');
    });

    it('offers no buttons while the write is in flight', () => {
      // Cancelling mid-write cannot un-write the tag, so it is not offered.
      expect(render('writing')).not.toContain('add-player-modal__actions');
    });
  });

  describe('after a failure', () => {
    it('shows what went wrong and offers a retry', () => {
      const html = render('failed', '7-iron', 'Tag not on the reader');

      expect(html).toContain('Could not write the tag');
      expect(html).toContain('Tag not on the reader');
      expect(html).toContain('Try again');
    });

    it('keeps the chosen club so it need not be picked again', () => {
      expect(render('failed', 'sw', 'Write failed at page 4')).not.toContain('picker-overlay');
    });
  });
});
