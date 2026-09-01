import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ClubChangeToast } from './ClubChangeToast';

describe('ClubChangeToast', () => {
  it('names the club in full rather than by its id', () => {
    const html = renderToString(<ClubChangeToast clubId="7-iron" onChangeTag={() => {}} />);

    expect(html).toContain('Club selected');
    expect(html).toContain('7 Iron');
    expect(html).not.toContain('7-iron');
  });

  it('puts the club name in its own element so it can be sized large', () => {
    const html = renderToString(<ClubChangeToast clubId="driver" onChangeTag={() => {}} />);

    expect(html).toMatch(/club-toast__club[^>]*>Driver</);
  });

  it('announces itself to assistive tech without stealing focus', () => {
    const html = renderToString(<ClubChangeToast clubId="pw" onChangeTag={() => {}} />);

    expect(html).toContain('role="status"');
    expect(html).toContain('aria-live="polite"');
  });

  it('offers Change tag without making the dimmed backdrop tappable', () => {
    const html = renderToString(<ClubChangeToast clubId="sw" onChangeTag={() => {}} />);

    expect(html).toContain('aria-label="Change the tag for Sand Wedge"');
    expect(html).toMatch(/club-toast__change[^>]*>Change tag</);
    expect(html).toMatch(/class="club-toast"[^>]*role="status"/);
  });

  it('falls back to the raw id for a club it does not recognize', () => {
    const html = renderToString(<ClubChangeToast clubId="mashie" onChangeTag={() => {}} />);

    expect(html).toContain('mashie');
  });
});
