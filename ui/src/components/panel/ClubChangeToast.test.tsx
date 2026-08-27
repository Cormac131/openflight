import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ClubChangeToast } from './ClubChangeToast';

describe('ClubChangeToast', () => {
  it('names the club in full rather than by its id', () => {
    const html = renderToString(<ClubChangeToast clubId="7-iron" />);

    expect(html).toContain('Club selected');
    expect(html).toContain('7 Iron');
    expect(html).not.toContain('7-iron');
  });

  it('puts the club name in its own element so it can be sized large', () => {
    const html = renderToString(<ClubChangeToast clubId="driver" />);

    expect(html).toMatch(/club-toast__club[^>]*>Driver</);
  });

  it('announces itself to assistive tech without stealing focus', () => {
    const html = renderToString(<ClubChangeToast clubId="pw" />);

    expect(html).toContain('role="status"');
    expect(html).toContain('aria-live="polite"');
  });

  it('has nothing tappable, so it cannot swallow a tap while it fades', () => {
    const html = renderToString(<ClubChangeToast clubId="sw" />);

    expect(html).not.toContain('<button');
  });

  it('falls back to the raw id for a club it does not recognize', () => {
    const html = renderToString(<ClubChangeToast clubId="mashie" />);

    expect(html).toContain('mashie');
  });
});
