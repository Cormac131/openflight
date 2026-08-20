import { useMemo, useState } from 'react';
import type { Shot } from '../../types/shot';
import { computeStats, computeSwingSpeedStats, getUniqueClubs, isSwingSpeedShot } from '../../types/shot';
import { useDragScroll } from '../../hooks/useDragScroll';
import { useUnitPreference } from '../../state/useUnitPreference';
import { formatDistance, formatSpeed, getDistanceUnit, getSpeedUnit } from '../../utils/units';
import { MetricCard } from '../ui/MetricCard';
import { PanelHeader } from './PanelHeader';

interface StatsPanelProps {
  shots: Shot[];
  activeClub: string;
  playerName: string;
}

interface StatTile {
  id: string;
  label: string;
  value: string;
  unit?: string;
}

/**
 * Design doc 7a: session summary as a hairline tile grid, with the per-club
 * filter chips above the tiles. Six tiles for a ball-strike session (3x2 as
 * drawn), four for a swing-speed one.
 */
export function StatsPanel({ shots, activeClub, playerName }: StatsPanelProps) {
  const hasShotsForActiveClub = shots.some((shot) => shot.club === activeClub);
  const [selectedClub, setSelectedClub] = useState<string | null>(hasShotsForActiveClub ? activeClub : null);
  const [prevActiveClub, setPrevActiveClub] = useState(activeClub);
  const chipScroll = useDragScroll<HTMLDivElement>('x');

  // Update state during render when the prop changes, rather than in an effect.
  if (activeClub !== prevActiveClub) {
    setPrevActiveClub(activeClub);
    setSelectedClub(hasShotsForActiveClub ? activeClub : null);
  }

  const { unitSystem } = useUnitPreference();
  const speedUnit = getSpeedUnit(unitSystem);
  const distanceUnit = getDistanceUnit(unitSystem);

  const availableClubs = useMemo(() => getUniqueClubs(shots), [shots]);
  const clubCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const shot of shots) {
      counts[shot.club] = (counts[shot.club] ?? 0) + 1;
    }
    return counts;
  }, [shots]);

  const filteredShots = useMemo(
    () => (selectedClub === null ? shots : shots.filter((shot) => shot.club === selectedClub)),
    [shots, selectedClub]
  );

  const stats = useMemo(() => computeStats(filteredShots), [filteredShots]);
  const swingStats = useMemo(() => computeSwingSpeedStats(filteredShots), [filteredShots]);
  const isSwingSpeedSession = filteredShots.length > 0 && filteredShots.every(isSwingSpeedShot);

  const tiles: StatTile[] = useMemo(() => {
    if (isSwingSpeedSession) {
      return [
        { id: 'count', label: 'Swings', value: String(swingStats.count) },
        { id: 'last', label: 'Last', value: formatSpeed(swingStats.last_speed_mph, unitSystem, 1), unit: speedUnit },
        { id: 'best', label: 'Best', value: formatSpeed(swingStats.best_speed_mph, unitSystem, 1), unit: speedUnit },
        { id: 'avg', label: 'Average', value: formatSpeed(swingStats.avg_speed_mph, unitSystem, 1), unit: speedUnit },
      ];
    }

    return [
      { id: 'count', label: 'Shots', value: String(stats.shot_count) },
      { id: 'avg_ball', label: 'Avg ball', value: formatSpeed(stats.avg_ball_speed, unitSystem, 1), unit: speedUnit },
      { id: 'max_ball', label: 'Max ball', value: formatSpeed(stats.max_ball_speed, unitSystem, 1), unit: speedUnit },
      {
        id: 'avg_carry',
        label: 'Avg carry',
        value: formatDistance(stats.avg_carry_est, unitSystem, 0),
        unit: distanceUnit,
      },
      {
        id: 'avg_club',
        label: 'Avg club',
        value: stats.avg_club_speed === null ? '—' : formatSpeed(stats.avg_club_speed, unitSystem, 1),
        unit: stats.avg_club_speed === null ? undefined : speedUnit,
      },
      {
        id: 'avg_smash',
        label: 'Avg smash',
        value: stats.avg_smash_factor === null ? '—' : stats.avg_smash_factor.toFixed(2),
      },
    ];
  }, [isSwingSpeedSession, stats, swingStats, unitSystem, speedUnit, distanceUnit]);

  const clubFilters = (
    <div className="stats-panel__chips" role="group" aria-label="Filter by club">
      <button
        type="button"
        className={`panel-chip${selectedClub === null ? ' panel-chip--active' : ''}`}
        aria-pressed={selectedClub === null}
        onClick={() => setSelectedClub(null)}
      >
        All ({shots.length})
      </button>
      <div
        className="panel-chips stats-panel__chip-scroll"
        ref={chipScroll.ref}
        onPointerDown={chipScroll.onPointerDown}
        onPointerMove={chipScroll.onPointerMove}
        onPointerUp={chipScroll.onPointerUp}
        onPointerCancel={chipScroll.onPointerCancel}
        onClickCapture={chipScroll.onClickCapture}
      >
        {availableClubs.map((club) => (
          <button
            key={club}
            type="button"
            className={`panel-chip${selectedClub === club ? ' panel-chip--active' : ''}`}
            aria-pressed={selectedClub === club}
            onClick={() => setSelectedClub(club)}
          >
            {club.toUpperCase()} ({clubCounts[club] ?? 0})
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div className="panel">
      <PanelHeader
        title="Stats"
        subtitle={playerName}
        actions={<span className="panel-header__meta">{`${speedUnit} / ${distanceUnit}`}</span>}
      />
      <div className="panel__body stats-panel">
        {clubFilters}
        {shots.length === 0 ? (
          <div className="panel__body--empty">
            <span className="panel__empty-title">No shots yet</span>
            <span className="panel__empty-detail">Session stats appear after your first shot</span>
          </div>
        ) : (
          <div className={`stats-panel__grid stats-panel__grid--of-${tiles.length}`}>
            {tiles.map((tile) => (
              <MetricCard key={tile.id} label={tile.label} value={tile.value} unit={tile.unit} labelPosition="above" />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
