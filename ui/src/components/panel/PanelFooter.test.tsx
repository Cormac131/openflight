import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { PanelFooter } from './PanelFooter';
import type { PanelView } from './views';

function render(currentView: PanelView = 'live', shotCount = 0) {
  return renderToString(
    <PanelFooter
      currentView={currentView}
      onChangeView={() => {}}
      onOpenMenu={() => {}}
      menuOpen={false}
      shotCount={shotCount}
      cameraStreaming={false}
      ballDetected={false}
      debugRecording={false}
    />
  ).replace(/<!-- -->/g, '');
}

describe('PanelFooter', () => {
  it('puts units on the right of Live without a shot count', () => {
    const html = render('live', 55);

    expect(html).toContain('panel-footer__meta');
    expect(html).toContain('panel-footer__units');
    expect(html).toContain('mph / yds');
    expect(html).not.toContain('Shot 55');
    expect(html).toContain('nav__badge">55<');
  });

  it('puts units on Stats and Shots without a footer shot count', () => {
    for (const view of ['stats', 'shots'] as const) {
      const html = render(view, 4);

      expect(html).toContain('mph / yds');
      expect(html).not.toContain('Shot 04');
      expect(html).toContain('nav__badge">4<');
    }
  });

  it('hides units on Players, Camera, and Debug', () => {
    for (const view of ['players', 'camera', 'debug'] as const) {
      const html = render(view, 4);

      expect(html).not.toContain('panel-footer__meta');
      expect(html).not.toContain('mph / yds');
    }
  });

  it('marks the active tab pressed', () => {
    const html = render('live');
    const liveButton = html.match(/<button[^>]*nav__button[^>]*>[\s\S]*?<span>Live<\/span>[\s\S]*?<\/button>/)?.[0];

    expect(liveButton).toBeDefined();
    expect(liveButton).toContain('nav__button--active');
    expect(liveButton).toContain('aria-pressed="true"');
  });

  it('separates tabs with the same hairline divider as the header', () => {
    const html = render('live');

    expect(html).toContain('panel-footer__nav');
    expect(html).toContain('panel-footer__tabs');
    expect(html).toContain('aria-label="Panels"');
    expect(html.match(/panel-header__divider/g)).toHaveLength(5);
  });
});
