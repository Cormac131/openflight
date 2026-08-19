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

  it('shows an estimated mark on the title, not beside the value', () => {
    const estimated = renderToString(
      <MetricCard value="8.9" unit="°" label="V. launch" labelPosition="above" estimated />
    );
    const labelIdx = estimated.indexOf('metric-card__label');
    const markIdx = estimated.indexOf('metric-card__estimated');
    const valueIdx = estimated.indexOf('metric-card__value-row');
    expect(markIdx).toBeGreaterThan(labelIdx);
    expect(markIdx).toBeLessThan(valueIdx);

    const measured = renderToString(<MetricCard value="140.7" unit="mph" label="Ball speed" />);
    expect(measured).not.toContain('metric-card__estimated');
  });

  it('always reserves a meta slot on label-above tiles so values share a row', () => {
    const empty = renderToString(<MetricCard value="-2.9" unit="°" label="Club path" labelPosition="above" />);
    const dense = renderToString(
      <MetricCard
        value="8.9"
        unit="°"
        label="V. launch"
        labelPosition="above"
        subtext="estimated"
        confidence="high"
      />
    );

    expect(empty).toContain('metric-card__meta');
    expect(dense).toContain('metric-card__meta');
  });
});
