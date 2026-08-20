import { useEffect, useMemo, useState } from 'react';
import type { Shot } from '../../types/shot';
import { computeSwingSpeedStats } from '../../types/shot';
import { useUnitPreference } from '../../state/useUnitPreference';
import { getDistanceUnit, getSpeedUnit } from '../../utils/units';
import { MetricCard, EstimatedMark } from '../ui/MetricCard';
import { PanelHeader } from './PanelHeader';
import { buildLiveMetrics, pinSelectedMetric, SPOTLIGHT_DURATION_MS } from './liveMetrics';

interface LivePanelProps {
  shot: Shot | null;
  shots: Shot[];
  playerName: string;
  clubLabel: string;
  /** Undefined outside swing-speed mode. Scopes the swing stats to one implement. */
  activeTrainingImplement?: string;
  /** Fires the Launch Daddy secret tap, which now lives on the status dot. */
  onStatusTap?: () => void;
  /** Metric pinned top-left and shown full-screen after a new shot. */
  selectedMetricId?: string | null;
  onSelectMetric?: (id: string) => void;
  /** True for a freshly captured shot (not a restored session). */
  isNewShot?: boolean;
  /** Camera ball-detection is running. */
  ballDetectionEnabled?: boolean;
  /** YOLO currently sees a ball. */
  ballDetected?: boolean;
}

/**
 * Ten-metric table. Tapping a tile selects it: the title turns accent yellow,
 * the tile moves to the top-left, and that metric fills the screen for
 * {@link SPOTLIGHT_DURATION_MS} after the next shot (tap to dismiss early).
 */
export function LivePanel({
  shot,
  shots,
  playerName,
  clubLabel,
  activeTrainingImplement,
  onStatusTap,
  selectedMetricId = null,
  onSelectMetric,
  isNewShot = false,
  ballDetectionEnabled = false,
  ballDetected = false,
}: LivePanelProps) {
  const { unitSystem } = useUnitPreference();
  // App remounts this panel on `shotVersion`, so a new shot starts with the
  // spotlight open. The effect only hides it after 10s — it must not call
  // setState synchronously, and it must not depend on `isNewShot` (that flag
  // clears at 2.5s).
  const [spotlightOpen, setSpotlightOpen] = useState(isNewShot);

  const swingStats = useMemo(
    () => computeSwingSpeedStats(shots, { playerName, trainingImplement: activeTrainingImplement }),
    [shots, playerName, activeTrainingImplement]
  );
  const metrics = useMemo(
    () => (shot ? pinSelectedMetric(buildLiveMetrics(shot, unitSystem, swingStats), selectedMetricId) : []),
    [shot, unitSystem, swingStats, selectedMetricId]
  );
  const selected = metrics[0] ?? null;
  const showBallWarning = ballDetectionEnabled && !ballDetected;
  const ballWarning = showBallWarning ? (
    <div className="live-panel__ball-warning" role="alert">
      <span className="live-panel__ball-warning-title">No ball detected</span>
      <span className="live-panel__ball-warning-detail">Place a ball in the camera view before swinging</span>
    </div>
  ) : null;

  useEffect(() => {
    if (!spotlightOpen) {
      return;
    }

    const timer = setTimeout(() => setSpotlightOpen(false), SPOTLIGHT_DURATION_MS);
    return () => clearTimeout(timer);
  }, [spotlightOpen]);

  const unitsLabel = `${getSpeedUnit(unitSystem)} / ${getDistanceUnit(unitSystem)}`;
  const header = (
    <PanelHeader
      title="Live"
      subtitle={playerName}
      club={clubLabel}
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

  if (!selected) {
    return (
      <div className="panel">
        {header}
        <div className="panel__body panel__body--empty live-panel__empty">
          {ballWarning}
          <span className="panel__empty-title live-panel__empty-title">Ready</span>
          <span className="panel__empty-detail live-panel__empty-detail">
            Start a shot or swing speed session
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="panel">
      {header}
      <div className="panel__body live-panel__body">
        {ballWarning}
        {spotlightOpen ? (
          <button
            type="button"
            className="live-panel__spotlight"
            aria-label="Hide shot overlay"
            onClick={() => setSpotlightOpen(false)}
          >
            <span className="live-panel__spotlight-label">
              {selected.label} · {clubLabel}
              {selected.estimated ? <EstimatedMark /> : null}
            </span>
            <div className="live-panel__spotlight-value-row">
              <span className="live-panel__spotlight-value">{selected.value}</span>
              {selected.unit ? <span className="live-panel__spotlight-unit">{selected.unit}</span> : null}
            </div>
            {selected.subtext ? <span className="live-panel__spotlight-subtext">{selected.subtext}</span> : null}
          </button>
        ) : null}
        <div className={`live-panel__grid live-panel__grid--of-${metrics.length}`}>
          {metrics.map((metric) => (
            <MetricCard
              key={metric.id}
              label={metric.label}
              value={metric.value}
              unit={metric.unit}
              subtext={metric.subtext}
              estimated={metric.estimated}
              confidence={metric.confidence}
              labelPosition="above"
              selected={metric.id === selected.id}
              onClick={onSelectMetric ? () => onSelectMetric(metric.id) : undefined}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
