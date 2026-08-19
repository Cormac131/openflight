import type { Shot, SpinQuality, SwingSpeedStats } from '../../types/shot';
import { getSwingSpeedMph, isSwingSpeedShot } from '../../types/shot';
import type { UnitSystem } from '../../utils/units';
import { formatDistance, formatSpeed, getDistanceUnit, getSpeedUnit } from '../../utils/units';

/** Placeholder for a metric the current shot has no value for. */
export const NO_VALUE = '—';

export interface LiveMetric {
  /** Stable key. Persisted as the promoted-hero choice, so do not rename. */
  id: string;
  label: string;
  value: string;
  unit?: string;
  subtext?: string;
  /** True when the value is modeled rather than measured. Rendered as an icon. */
  estimated?: boolean;
  confidence?: SpinQuality | null;
}

/**
 * Design doc 6a shows a hero slot plus a 4x2 tile grid: nine metrics, always the
 * same nine in the same order, so the grid never reflows between shots. Metrics
 * the shot did not produce render as {@link NO_VALUE} rather than disappearing.
 */
export const LIVE_METRIC_COUNT = 9;

/** Metric count for a swing-speed session: hero plus a single 4-tile row. */
export const SWING_METRIC_COUNT = 5;

function formatOptionalAngle(value: number | null, signed = false): string {
  if (value === null) return NO_VALUE;
  const prefix = signed && value >= 0 ? '+' : '';
  return `${prefix}${value.toFixed(1)}`;
}

function angleUnit(value: number | null): string | undefined {
  return value === null ? undefined : '°';
}

function formatSpinRpm(rpm: number | null): string {
  if (rpm === null) return NO_VALUE;
  return rpm.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function launchAngleQuality(confidence: number | null): SpinQuality | null {
  if (confidence === null) return null;
  if (confidence >= 0.7) return 'high';
  if (confidence >= 0.4) return 'medium';
  return 'low';
}

function shotShape(spinAxisDeg: number | null): string | undefined {
  if (spinAxisDeg === null) return undefined;
  if (spinAxisDeg > 2) return 'fade';
  if (spinAxisDeg < -2) return 'draw';
  return 'straight';
}

function markEstimated(isEstimated: boolean): true | undefined {
  return isEstimated ? true : undefined;
}

function buildBallStrikeMetrics(shot: Shot, unitSystem: UnitSystem): LiveMetric[] {
  const speedUnit = getSpeedUnit(unitSystem);
  const carry = shot.carry_spin_adjusted ?? shot.estimated_carry_yards;
  const angleConfidence = launchAngleQuality(shot.launch_angle_confidence);
  const angleEstimated = shot.angle_source === 'estimated';

  return [
    {
      id: 'ball_speed',
      label: 'Ball speed',
      value: formatSpeed(shot.ball_speed_mph, unitSystem, 1),
      unit: speedUnit,
    },
    {
      id: 'carry',
      label: 'Carry',
      value: formatDistance(carry, unitSystem, 0),
      unit: getDistanceUnit(unitSystem),
      subtext: shot.carry_spin_adjusted === null ? undefined : 'spin-adjusted',
      estimated: markEstimated(shot.carry_spin_adjusted === null),
    },
    {
      id: 'club_speed',
      label: 'Club speed',
      value: shot.club_speed_mph === null ? NO_VALUE : formatSpeed(shot.club_speed_mph, unitSystem, 1),
      unit: shot.club_speed_mph === null ? undefined : speedUnit,
    },
    {
      id: 'smash',
      label: 'Smash',
      value: shot.smash_factor === null ? NO_VALUE : shot.smash_factor.toFixed(2),
    },
    {
      id: 'launch_v',
      label: 'V. launch',
      value: formatOptionalAngle(shot.launch_angle_vertical),
      unit: angleUnit(shot.launch_angle_vertical),
      estimated: markEstimated(shot.launch_angle_vertical !== null && angleEstimated),
      confidence: shot.launch_angle_vertical === null ? null : angleConfidence,
    },
    {
      id: 'launch_h',
      label: 'H. launch',
      value: formatOptionalAngle(shot.launch_angle_horizontal, true),
      unit: angleUnit(shot.launch_angle_horizontal),
      estimated: markEstimated(shot.launch_angle_horizontal !== null && angleEstimated),
      confidence: shot.launch_angle_horizontal === null ? null : angleConfidence,
    },
    {
      id: 'spin',
      label: 'Spin',
      value: formatSpinRpm(shot.spin_rpm),
      unit: shot.spin_rpm === null ? undefined : 'rpm',
      estimated: markEstimated(shot.spin_rpm !== null && shot.spin_source === 'calculated'),
      confidence: shot.spin_rpm === null ? null : shot.spin_quality,
    },
    {
      id: 'spin_axis',
      label: 'Spin axis',
      value: formatOptionalAngle(shot.spin_axis_deg, true),
      unit: angleUnit(shot.spin_axis_deg),
      subtext: shotShape(shot.spin_axis_deg),
    },
    {
      id: 'club_path',
      label: 'Club path',
      value: formatOptionalAngle(shot.club_path_deg, true),
      unit: angleUnit(shot.club_path_deg),
    },
  ];
}

function buildSwingSpeedMetrics(shot: Shot, stats: SwingSpeedStats, unitSystem: UnitSystem): LiveMetric[] {
  const speedUnit = getSpeedUnit(unitSystem);

  return [
    {
      id: 'swing_last',
      label: 'Last swing',
      value: formatSpeed(getSwingSpeedMph(shot), unitSystem, 1),
      unit: speedUnit,
    },
    {
      id: 'swing_best',
      label: 'Best',
      value: formatSpeed(stats.best_speed_mph, unitSystem, 1),
      unit: speedUnit,
      subtext: 'player + implement',
    },
    {
      id: 'swing_avg',
      label: 'Average',
      value: formatSpeed(stats.avg_speed_mph, unitSystem, 1),
      unit: speedUnit,
      subtext: `${stats.count} swings`,
    },
    {
      id: 'swing_count',
      label: 'Swings',
      value: String(stats.count),
      subtext: shot.swing_speed_reading_count === undefined ? undefined : `${shot.swing_speed_reading_count} readings`,
    },
    {
      id: 'swing_implement',
      label: 'Implement',
      value: shot.training_implement_label ?? shot.club,
      subtext:
        shot.swing_speed_trigger_mph === undefined
          ? undefined
          : `${formatSpeed(shot.swing_speed_trigger_mph, unitSystem, 1)} ${speedUnit} trigger`,
    },
  ];
}

/**
 * Build the fixed metric list for the Live panel. Returns {@link LIVE_METRIC_COUNT}
 * entries for a ball-strike shot and {@link SWING_METRIC_COUNT} for a swing-speed
 * one; the two sets share no ids, so a promoted-hero choice never leaks across
 * modes.
 */
export function buildLiveMetrics(shot: Shot, unitSystem: UnitSystem, swingStats: SwingSpeedStats): LiveMetric[] {
  return isSwingSpeedShot(shot)
    ? buildSwingSpeedMetrics(shot, swingStats, unitSystem)
    : buildBallStrikeMetrics(shot, unitSystem);
}

/**
 * Split the metric list into the promoted hero and the remaining tiles. Falls
 * back to the first metric when `heroId` is absent from this list — which is the
 * normal case right after switching between ball-strike and swing-speed modes.
 */
export function splitHeroMetric(
  metrics: LiveMetric[],
  heroId: string | null
): { hero: LiveMetric | null; tiles: LiveMetric[] } {
  if (metrics.length === 0) {
    return { hero: null, tiles: [] };
  }

  const heroIndex = metrics.findIndex((metric) => metric.id === heroId);
  const index = heroIndex === -1 ? 0 : heroIndex;

  return {
    hero: metrics[index],
    tiles: metrics.filter((_, i) => i !== index),
  };
}
