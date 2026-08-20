import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import { PanelHeader } from './PanelHeader';

describe('PanelHeader', () => {
  it('marks the status dot disconnected by default', () => {
    const html = renderToString(<PanelHeader title="Live" />);

    expect(html).toContain('panel-header__dot--disconnected');
    expect(html).toContain('aria-label="Server disconnected"');
    expect(html).not.toContain('panel-header__dot--connected');
  });

  it('turns the status dot green when the server is connected', () => {
    const html = renderToString(<PanelHeader title="Live" connected />);

    expect(html).toContain('panel-header__dot--connected');
    expect(html).toContain('aria-label="Server connected"');
  });

  it('shows the club after the subtitle, each behind a divider', () => {
    const html = renderToString(<PanelHeader title="Live" subtitle="James" club="DR" />);

    expect(html).toContain('panel-header__subtitle">James<');
    expect(html).toContain('panel-header__club">DR<');
    expect(html.match(/panel-header__divider/g)).toHaveLength(2);
  });
});
