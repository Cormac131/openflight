import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ClubTagList } from './ClubTagList';
import type { ClubTag } from '../../types/nfc';

const tag: ClubTag = {
  uid: '04A2B1C3',
  uid_display: '04:A2:B1:C3',
  club: '7-iron',
  learned_at: '2026-01-01T00:00:00+00:00',
  last_seen_at: null,
};

describe('ClubTagList', () => {
  it('names the club in full rather than by its id', () => {
    const html = renderToString(<ClubTagList tags={[tag]} onForget={() => {}} />);

    expect(html).toContain('7 Iron');
    expect(html).not.toContain('7-iron');
  });

  it('shows the UID so a tag can be matched to the sticker on the grip', () => {
    const html = renderToString(<ClubTagList tags={[tag]} onForget={() => {}} />);

    expect(html).toContain('04:A2:B1:C3');
  });

  it('labels each forget button with its club, since UIDs are unreadable', () => {
    const html = renderToString(<ClubTagList tags={[tag]} onForget={() => {}} />);

    expect(html).toContain('aria-label="Forget the tag for 7 Iron"');
  });

  it('lists two tags taught the same club separately', () => {
    const second = { ...tag, uid: '04A2B1C4', uid_display: '04:A2:B1:C4' };

    const html = renderToString(<ClubTagList tags={[tag, second]} onForget={() => {}} />);

    expect(html).toContain('04:A2:B1:C3');
    expect(html).toContain('04:A2:B1:C4');
  });

  it('says so when nothing has been learned yet', () => {
    const html = renderToString(<ClubTagList tags={[]} onForget={() => {}} />);

    expect(html).toContain('No tags learned yet');
    expect(html).not.toContain('menu-sheet__chip');
  });

  it('bounds a full bag inside a drag-scrollable list', () => {
    const many = Array.from({ length: 14 }, (_, index) => ({
      ...tag,
      uid: `04A2B1${index.toString(16).padStart(2, '0').toUpperCase()}`,
    }));

    const html = renderToString(<ClubTagList tags={many} onForget={() => {}} />);

    // Kiosk convention: an overflowing list scrolls by dragging its content.
    expect(html).toContain('class="club-tag-list"');
  });
});
