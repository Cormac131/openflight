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

function render(shot: Shot | null, shots: Shot[] = shot ? [shot] : [], heroMetricId: string | null = null) {
  return text(
    renderToString(
      <LivePanel
        shot={shot}
        shots={shots}
        playerName="James"
        clubLabel="DR"
        heroMetricId={heroMetricId}
        onPromoteMetric={() => {}}
      />
    )
  );
}

describe('LivePanel', () => {
  it('shows the ready state before the first shot', () => {
    const html = render(null);

    expect(html).toContain('Ready');
    expect(html).not.toContain('live-panel__hero-value');
  });

  it('renders the hero beside eight tiles', () => {
    const html = render(makeShot());

    expect(html).toContain('live-panel__hero-value">92.0<');
    expect(html).toContain('Ball speed · DR');
    expect(html.match(/metric-card--interactive/g)).toHaveLength(8);
    expect(html).toContain('live-panel__grid--of-8');
  });

  it('promotes the selected metric into the hero slot', () => {
    const html = render(makeShot(), undefined, 'spin');

    expect(html).toContain('live-panel__hero-value">2,650<');
    expect(html).toContain('Spin · DR');
    // Ball speed is demoted to a tile rather than lost.
    expect(html).toContain('>Ball speed<');
    expect(html.match(/metric-card--interactive/g)).toHaveLength(8);
  });

  it('falls back to ball speed when the selected metric is not in this set', () => {
    // Happens right after switching between ball-strike and swing-speed modes.
    const html = render(makeShot(), undefined, 'swing_best');

    expect(html).toContain('live-panel__hero-value">92.0<');
  });

  it('marks estimated tiles with an icon instead of provenance copy', () => {
    const html = render(makeShot({ angle_source: 'estimated', spin_source: 'calculated' }));

    expect(html).toContain('metric-card__estimated');
    expect(html).not.toContain('>estimated<');
    expect(html).not.toContain('>radar<');
  });

  it('makes every tile a pressable button so it can be promoted', () => {
    const html = render(makeShot());

    // 6a's tiles are buttons; a non-interactive grid would break hero swapping.
    expect(html.match(/<button[^>]*metric-card--interactive/g)).toHaveLength(8);
    expect(html).toContain('aria-pressed="false"');
  });

  it('shows the shot number and unit label in the header', () => {
    const html = render(makeShot(), [makeShot(), makeShot(), makeShot()]);

    expect(html).toContain('mph / yds');
    expect(html).toContain('Shot 03');
    expect(html).toContain('panel-header__subtitle">James<');
  });

  it('renders the four-tile grid for a swing-speed shot', () => {
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
          onPromoteMetric={() => {}}
        />
      )
    );

    expect(html).toContain('live-panel__grid--of-4');
    expect(html.match(/metric-card--interactive/g)).toHaveLength(4);
    expect(html).toContain('Last swing · Stack 100g');
  });
});
