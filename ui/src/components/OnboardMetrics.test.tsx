import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { IWROnboardMetric, IWROnboardResult } from '../types/shot';
import { OnboardMetrics } from './OnboardMetrics';

function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function metric(value: number | null, overrides: Partial<IWROnboardMetric> = {}): IWROnboardMetric {
  const valid = value !== null;
  return {
    value,
    confidence: valid ? 0.8 : 0,
    label: valid ? 'MEASURED' : '-',
    measured: valid,
    radial_only: false,
    implausible: false,
    fallback: false,
    usable: valid,
    ...overrides,
  };
}

function result(overrides: Partial<IWROnboardResult> = {}): IWROnboardResult {
  return {
    version: 1,
    shot_id: 7,
    verdict: 'valid',
    impact_source: 'geometry',
    impact_timestamp_us: 23218,
    club_points: 7,
    ball_points: 6,
    smash: 1.5,
    quality: ['ball_locked'],
    metrics: {
      ball_speed: metric(60),
      vertical_launch: metric(12),
      horizontal_launch: metric(-1.5),
      club_speed: metric(40, { radial_only: true }),
      club_path: metric(2, { measured: false, label: 'ESTIMATED', confidence: 0.4 }),
      angle_of_attack: metric(null),
      spin_rate: metric(null),
      spin_axis: metric(null),
      impact_range: metric(1.36),
    },
    ...overrides,
  };
}

describe('OnboardMetrics', () => {
  it('renders nothing when the shot has no onboard result', () => {
    expect(renderToString(<OnboardMetrics result={null} />)).toBe('');
    expect(renderToString(<OnboardMetrics result={undefined} />)).toBe('');
  });

  it('shows the verdict, point counts and every metric in display units', () => {
    const html = text(renderToString(<OnboardMetrics result={result()} />));
    expect(html).toContain('onboard-metrics--valid');
    expect(html).toContain('Valid');
    expect(html).toContain('Shot 7');
    expect(html).toContain('7 club / 6 ball points');
    expect(html).toContain('Smash 1.50');
    expect(html).toContain('134.2'); // 60 m/s ball speed in mph
    expect(html).toContain('89.5'); // 40 m/s club speed
    expect(html).toContain('12.0');
    expect(html).toContain('-1.5');
  });

  it('labels measured and estimated metrics with their confidence, never blurring them', () => {
    const html = text(renderToString(<OnboardMetrics result={result()} />));
    expect(html).toContain('onboard-metrics__tag--measured');
    expect(html).toContain('Measured 80%');
    expect(html).toContain('onboard-metrics__tag--estimated');
    expect(html).toContain('Estimated 40%');
    expect(html).toContain('title="radial only"');
  });

  it('shows a dash, and no provenance tag, for invalid or implausible metrics', () => {
    const html = text(
      renderToString(
        <OnboardMetrics
          result={result({
            verdict: 'partial',
            metrics: {
              ball_speed: metric(20, { implausible: true, usable: false }),
              angle_of_attack: metric(null),
            },
          })}
        />
      )
    );
    expect(html).toContain('onboard-metrics--partial');
    expect(html).toContain('Partial');
    expect(html).not.toContain('44.7'); // the implausible 20 m/s is not shown
    expect((html.match(/onboard-metrics__item--empty/g) ?? []).length).toBe(6);
    expect(html).not.toContain('onboard-metrics__tag');
  });

  it('marks an invalid result and omits the smash when the firmware had none', () => {
    const html = text(renderToString(<OnboardMetrics result={result({ verdict: 'invalid', smash: null })} />));
    expect(html).toContain('onboard-metrics--invalid');
    expect(html).toContain('Invalid');
    expect(html).not.toContain('Smash');
  });
});
