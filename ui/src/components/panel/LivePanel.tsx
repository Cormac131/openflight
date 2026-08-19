import { useMemo } from 'react';
import type { Shot } from '../../types/shot';
import { computeSwingSpeedStats } from '../../types/shot';
import { useUnitPreference } from '../../state/useUnitPreference';
import { getDistanceUnit, getSpeedUnit } from '../../utils/units';
import { MetricCard, EstimatedMark } from '../ui/MetricCard';
import { PanelHeader } from './PanelHeader';
import { buildLiveMetrics, splitHeroMetric } from './liveMetrics';

interface LivePanelProps {
  shot: Shot | null;
  shots: Shot[];
  playerName: string;
  clubLabel: string;
  /** Undefined outside swing-speed mode. Scopes the swing stats to one implement. */
  activeTrainingImplement?: string;
  /** Fires the Launch Daddy secret tap, which now lives on the status dot. */
  onStatusTap?: () => void;
  /** Metric promoted into the hero slot. Unknown ids fall back to the first. */
  heroMetricId?: string | null;
  onPromoteMetric?: (id: string) => void;
}

/**
 * Design doc 6a: hero metric beside a hairline 4x2 tile grid. Tapping a tile
 * promotes it into the hero slot.
 *
 * The promoted id is a prop rather than read from `useHeroMetricStore` directly
 * so this stays a pure function of its inputs — zustand serves the *initial*
 * state during server rendering, which would make the hero untestable.
 */
export function LivePanel({
  shot,
  shots,
  playerName,
  clubLabel,
  activeTrainingImplement,
  onStatusTap,
  heroMetricId = null,
  onPromoteMetric,
}: LivePanelProps) {
  const { unitSystem } = useUnitPreference();

  const swingStats = useMemo(
    () => computeSwingSpeedStats(shots, { playerName, trainingImplement: activeTrainingImplement }),
    [shots, playerName, activeTrainingImplement]
  );
  const metrics = useMemo(
    () => (shot ? buildLiveMetrics(shot, unitSystem, swingStats) : []),
    [shot, unitSystem, swingStats]
  );
  const { hero, tiles } = useMemo(() => splitHeroMetric(metrics, heroMetricId), [metrics, heroMetricId]);

  const unitsLabel = `${getSpeedUnit(unitSystem)} / ${getDistanceUnit(unitSystem)}`;
  const header = (
    <PanelHeader
      title="Live"
      subtitle={playerName}
      onStatusTap={onStatusTap}
      actions={
        <>
          <span className="panel-header__meta">{unitsLabel}</span>
          <span className="panel-header__meta panel-header__meta--faint">
            Shot {String(shots.length).padStart(2, '0')}
          </span>
        </>
      }
    />
  );

  if (!hero) {
    return (
      <div className="panel">
        {header}
        <div className="panel__body panel__body--empty">
          <span className="panel__empty-title">Ready</span>
          <span className="panel__empty-detail">Start a shot or swing speed session</span>
        </div>
      </div>
    );
  }

  return (
    <div className="panel">
      {header}
      <div className="panel__body live-panel__body">
        <div className="live-panel__hero">
          <span className="live-panel__hero-label">
            {hero.label} · {clubLabel}
            {hero.estimated ? <EstimatedMark /> : null}
          </span>
          <div className="live-panel__hero-value-row">
            <span className="live-panel__hero-value">{hero.value}</span>
            {hero.unit ? <span className="live-panel__hero-unit">{hero.unit}</span> : null}
          </div>
          <span className="live-panel__hero-rule" aria-hidden="true" />
          {hero.subtext ? <span className="live-panel__hero-subtext">{hero.subtext}</span> : null}
        </div>
        <div className={`live-panel__grid live-panel__grid--of-${tiles.length}`}>
          {tiles.map((metric) => (
            <MetricCard
              key={metric.id}
              label={metric.label}
              value={metric.value}
              unit={metric.unit}
              subtext={metric.subtext}
              estimated={metric.estimated}
              confidence={metric.confidence}
              labelPosition="above"
              onClick={onPromoteMetric ? () => onPromoteMetric(metric.id) : undefined}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
