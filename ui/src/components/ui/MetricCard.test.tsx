import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MetricCard } from './MetricCard';

describe('MetricCard', () => {
  it('renders value, unit, and label', () => {
    const html = renderToString(<MetricCard value="92.0" unit="mph" label="Ball Speed" />);
    expect(html).toContain('92.0');
    expect(html).toContain('mph');
    expect(html).toContain('Ball Speed');
    expect(html).toContain('metric-card--default');
  });

  it('marks emphasis and hero variants', () => {
    const html = renderToString(
      <MetricCard value="133" unit="yds" label="Est. Carry" variant="emphasis" size="hero" />
    );
    expect(html).toContain('metric-card--emphasis');
    expect(html).toContain('metric-card--hero');
  });

  it('labels experimental spin without confidence dots', () => {
    const html = renderToString(<MetricCard value="8,750" unit="rpm" label="Spin Rate" confidence="experimental" />);
    expect(html).toContain('metric-card__confidence--experimental');
    expect(html).not.toContain('metric-card__confidence-dots');
  });
});
