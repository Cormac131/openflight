import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import App from './App';

describe('App chrome', () => {
  it('renders theme and unit segmented controls without TEST | DR', () => {
    const html = renderToString(<App />);

    expect(html).toContain('aria-label="Theme"');
    expect(html).toContain('DARK');
    expect(html).toContain('LIGHT');
    expect(html).toContain('aria-label="Display units"');
    expect(html).toContain('MPH/YDS');
    expect(html).toContain('KMH/M');
    expect(html).toContain('segmented-control');
    expect(html).not.toContain('unit-toggle');
    expect(html).not.toContain('TEST | DR');
  });

  it('marks the Live tab pressed', () => {
    const html = renderToString(<App />);
    const liveButton = html.match(/<button[^>]*nav__button[^>]*>[\s\S]*?<span>Live<\/span>[\s\S]*?<\/button>/)?.[0];

    expect(liveButton).toBeDefined();
    expect(liveButton).toContain('aria-pressed="true"');
    expect(liveButton).not.toContain('DARK');
  });
});
