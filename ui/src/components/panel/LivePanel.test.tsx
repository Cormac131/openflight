import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Shot } from '../../types/shot';
import { LivePanel } from './LivePanel';

/** React SSR splits interpolated text with comment markers; drop them. */
function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function makeShot(overrides: Partial<Shot> = {}): Shot {
  return {
    ball_speed_mph: 92,
    club_speed_mph: 68,
    smash_factor: 1.35,
    estimated_carry_yards: 210,
    carry_range: [205, 215],
    club: 'driver',
    timestamp: '2026-08-19T10:00:00Z',
    peak_magnitude: 100,
    launch_angle_vertical: 13.4,
    launch_angle_horizontal: -1.2,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: 2.1,
    club_path_deg: -0.6,
    spin_axis_deg: 3.4,
    spin_rpm: 2650,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    spin_method: null,
    carry_spin_adjusted: 214,
    ...overrides,
  };
}

function render(
  shot: Shot | null,
  shots: Shot[] = shot ? [shot] : [],
  selectedMetricId: string | null = null,
  isNewShot = false
) {
  return text(
    renderToString(
      <LivePanel
        shot={shot}
        shots={shots}
        playerName="James"
        clubLabel="DR"
        selectedMetricId={selectedMetricId}
        onSelectMetric={() => {}}
        isNewShot={isNewShot}
      />
    )
  );
}

function tileLabels(html: string): string[] {
  return [...html.matchAll(/metric-card__label[^>]*>([^<]+)/g)].map((match) => match[1]);
}

describe('LivePanel', () => {
  it('shows the ready state before the first shot', () => {
    const html = render(null);

    expect(html).toContain('Ready');
    expect(html).toContain('live-panel__empty-title');
    expect(html).not.toContain('live-panel__spotlight');
    expect(html).not.toContain('metric-card--interactive');
  });

  it('renders all ten metrics in a table with no hero slot', () => {
    const html = render(makeShot());

    expect(html).not.toContain('live-panel__hero');
    expect(html).toContain('live-panel__grid--of-10');
    expect(html.match(/metric-card--interactive/g)).toHaveLength(10);
    expect(html).toContain('>Club AoA<');
    expect(tileLabels(html)[0]).toBe('Ball speed');
  });

  it('pins the selected metric to the top left and marks its title selected', () => {
    const html = render(makeShot(), undefined, 'spin');

    expect(tileLabels(html)[0]).toBe('Spin');
    expect(html).toContain('metric-card--selected');
    expect(html).toMatch(/metric-card--selected[\s\S]*?>Spin</);
    expect(html).toContain('>Ball speed<');
    expect(html.match(/metric-card--interactive/g)).toHaveLength(10);
  });

  it('falls back to ball speed when the selected metric is not in this set', () => {
    const html = render(makeShot(), undefined, 'swing_best');

    expect(tileLabels(html)[0]).toBe('Ball speed');
    expect(html).toMatch(/metric-card--selected[\s\S]*?>Ball speed</);
  });

  it('shows the selected metric full-screen after a new shot', () => {
    const html = render(makeShot(), undefined, 'spin', true);

    expect(html).toContain('live-panel__spotlight');
    expect(html).toContain('live-panel__spotlight-label">Spin');
    expect(html).toContain('live-panel__spotlight-value">2,650<');
    expect(html).toContain('live-panel__grid--of-10');
    expect(html).toMatch(/<button[^>]*live-panel__spotlight/);
    expect(html).toContain('aria-label="Hide shot overlay"');
  });

  it('does not show the spotlight for a restored session shot', () => {
    const html = render(makeShot(), undefined, null, false);

    expect(html).not.toContain('live-panel__spotlight');
  });

  it('marks estimated tiles with an icon instead of provenance copy', () => {
    const html = render(makeShot({ angle_source: 'estimated', spin_source: 'calculated' }));

    expect(html).toContain('metric-card__estimated');
    expect(html).not.toContain('>estimated<');
    expect(html).not.toContain('>radar<');
  });

  it('makes every tile a pressable button so it can be selected', () => {
    const html = render(makeShot());

    expect(html.match(/<button[^>]*metric-card--interactive/g)).toHaveLength(10);
    expect(html).toContain('aria-pressed="true"');
  });

  it('shows the shot number and unit label in the header', () => {
    const html = render(makeShot(), [makeShot(), makeShot(), makeShot()]);

    expect(html).toContain('mph / yds');
    expect(html).toContain('Shot 03');
    expect(html).toContain('panel-header__subtitle">James<');
    expect(html).toContain('panel-header__club">DR<');
  });

  it('renders the five-tile grid for a swing-speed shot', () => {
    const swing = makeShot({
      mode: 'swing-speed',
      training_implement: 'stack-100g',
      training_implement_label: 'Stack 100g',
      player_name: 'James',
    });
    const html = text(
      renderToString(
        <LivePanel
          shot={swing}
          shots={[swing]}
          playerName="James"
          clubLabel="Stack 100g"
          activeTrainingImplement="stack-100g"
          onSelectMetric={() => {}}
        />
      )
    );

    expect(html).not.toContain('live-panel__hero');
    expect(html).toContain('live-panel__grid--of-5');
    expect(html.match(/metric-card--interactive/g)).toHaveLength(5);
    expect(tileLabels(html)[0]).toBe('Last swing');
  });

  it('does not warn when ball detection is off', () => {
    expect(render(makeShot())).not.toContain('live-panel__ball-warning');
    expect(render(null)).not.toContain('live-panel__ball-warning');
  });

  it('shows a full-screen warning when ball detection is on and no ball is found', () => {
    const html = text(
      renderToString(
        <LivePanel
          shot={makeShot()}
          shots={[makeShot()]}
          playerName="James"
          clubLabel="DR"
          ballDetectionEnabled
          ballDetected={false}
        />
      )
    );

    expect(html).toContain('live-panel__ball-warning');
    expect(html).toContain('No ball detected');
    expect(html).toContain('role="alert"');
  });

  it('still warns on the ready screen so a swing is not taken without a ball', () => {
    const html = text(
      renderToString(
        <LivePanel shot={null} shots={[]} playerName="James" clubLabel="DR" ballDetectionEnabled ballDetected={false} />
      )
    );

    expect(html).toContain('No ball detected');
  });

  it('hides the warning once a ball is detected', () => {
    const html = text(
      renderToString(
        <LivePanel
          shot={makeShot()}
          shots={[makeShot()]}
          playerName="James"
          clubLabel="DR"
          ballDetectionEnabled
          ballDetected
        />
      )
    );

    expect(html).not.toContain('live-panel__ball-warning');
  });
});
