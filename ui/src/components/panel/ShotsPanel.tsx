import { useMemo, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import type { Shot } from '../../types/shot';
import { getSwingSpeedMph, isSwingSpeedShot } from '../../types/shot';
import { useUnitPreference } from '../../state/useUnitPreference';
import { useSystemStore } from '../../stores/useSystemStore';
import { getEmptyValidationEntry, useValidationStore, type ValidationEntry } from '../../stores/useValidationStore';
import { socketService } from '../../services/socketService';
import type { UnitSystem } from '../../utils/units';
import { formatDistance, formatSpeed } from '../../utils/units';
import { buildValidationCsv, comparatorDifference, downloadCsv } from '../../utils/validationCsv';
import { PanelHeader } from './PanelHeader';

interface ShotsPanelProps {
  shots: Shot[];
  playerName: string;
  clubLabel?: string;
  onDeleteShot: (timestamp: string) => void;
}

const COMPARATOR_DEVICES = ['Stack Radar', 'PRGR', 'TrackMan', 'Full Swing', 'Other'];

function optionalNumber(value: number | null | undefined, digits = 1, signed = false): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  const prefix = signed && value >= 0 ? '+' : '';
  return `${prefix}${value.toFixed(digits)}`;
}

/** The five numeric columns of a row, in the order design doc 7b draws them. */
function rowValues(shot: Shot, unitSystem: UnitSystem): string[] {
  if (isSwingSpeedShot(shot)) {
    // A swing-speed shot has no ball flight; reuse the columns for its own data
    // so the grid stays aligned with ball-strike rows.
    return [
      formatSpeed(getSwingSpeedMph(shot), unitSystem, 1),
      '—',
      optionalNumber(shot.swing_speed_reading_count, 0),
      optionalNumber(shot.swing_speed_trigger_mph),
      optionalNumber(shot.swing_speed_duration_ms, 0),
    ];
  }

  return [
    formatSpeed(shot.ball_speed_mph, unitSystem, 1),
    shot.club_speed_mph === null ? '—' : formatSpeed(shot.club_speed_mph, unitSystem, 1),
    optionalNumber(shot.launch_angle_vertical),
    shot.spin_rpm === null ? '—' : shot.spin_rpm.toLocaleString('en-US', { maximumFractionDigits: 0 }),
    formatDistance(shot.estimated_carry_yards, unitSystem, 0),
  ];
}

function ValidationEditor({
  shot,
  entry,
  onUpdate,
}: {
  shot: Shot;
  entry: ValidationEntry;
  onUpdate: (timestamp: string, patch: Partial<ValidationEntry>) => void;
}) {
  const difference = comparatorDifference(shot, entry);

  return (
    <div className="shots-panel__validation">
      <label className="shots-panel__field">
        <span>Device</span>
        <select
          value={entry.comparatorDevice}
          onChange={(event) => onUpdate(shot.timestamp, { comparatorDevice: event.target.value })}
        >
          <option value="">Device</option>
          {COMPARATOR_DEVICES.map((device) => (
            <option key={device} value={device}>
              {device}
            </option>
          ))}
        </select>
      </label>
      <label className="shots-panel__field">
        <span>Comparator</span>
        <input
          type="number"
          inputMode="decimal"
          min="0"
          step="0.1"
          placeholder="mph"
          value={entry.comparatorSpeed}
          onChange={(event) => onUpdate(shot.timestamp, { comparatorSpeed: event.target.value })}
        />
      </label>
      <div className="shots-panel__field shots-panel__field--diff">
        <span>Diff</span>
        <strong>{difference === null ? '—' : `${difference >= 0 ? '+' : ''}${difference.toFixed(1)}`}</strong>
      </div>
      <label className="shots-panel__field shots-panel__field--notes">
        <span>Notes</span>
        <input
          type="text"
          placeholder="notes…"
          value={entry.notes}
          onChange={(event) => onUpdate(shot.timestamp, { notes: event.target.value })}
        />
      </label>
    </div>
  );
}

/**
 * Design doc 7b: session log as a hairline table with Upload / Export in the
 * header.
 *
 * 7b has no room for the per-shot validation fields the old ShotList showed
 * inline, so a row expands on tap to reveal them — the mockup's own "make the
 * shot rows tappable to open shot detail" follow-up.
 */
export function ShotsPanel({ shots, playerName, clubLabel, onDeleteShot }: ShotsPanelProps) {
  const { unitSystem } = useUnitPreference();
  const { entries, updateEntry, removeEntry } = useValidationStore();
  const [expanded, setExpanded] = useState<string | null>(null);
  const { cloudUploadState, cloudUploadMessage } = useSystemStore(
    useShallow((state) => ({
      cloudUploadState: state.cloudUploadState,
      cloudUploadMessage: state.cloudUploadMessage,
    }))
  );

  const visibleShots = useMemo(() => [...shots].reverse(), [shots]);
  const validatedCount = useMemo(
    () => shots.filter((shot) => entries[shot.timestamp]?.comparatorSpeed).length,
    [entries, shots]
  );

  const handleExport = () => {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    downloadCsv(`openflight-validation-${stamp}.csv`, buildValidationCsv(shots, entries));
  };

  const handleDelete = (timestamp: string) => {
    removeEntry(timestamp);
    onDeleteShot(timestamp);
  };

  const header = (
    <PanelHeader
      title="Shots"
      subtitle={
        shots.length === 0
          ? playerName
          : cloudUploadMessage || `${shots.length} recorded · ${validatedCount}/${shots.length} validated`
      }
      club={clubLabel}
      actions={
        <>
          <button
            type="button"
            className="panel-chip"
            disabled={cloudUploadState === 'running' || shots.length === 0}
            onClick={() => socketService.uploadCloud()}
          >
            {cloudUploadState === 'running' ? 'Uploading' : 'Upload cloud'}
          </button>
          <button type="button" className="panel-chip" disabled={shots.length === 0} onClick={handleExport}>
            Export CSV
          </button>
        </>
      }
    />
  );

  if (shots.length === 0) {
    return (
      <div className="panel">
        {header}
        <div className="panel__body panel__body--empty">
          <span className="panel__empty-title">No shots yet</span>
          <span className="panel__empty-detail">Recorded shots appear here</span>
        </div>
      </div>
    );
  }

  return (
    <div className="panel">
      {header}
      <div className="shots-panel__columns" role="presentation">
        <span>Shot</span>
        <span>Player</span>
        <span className="shots-panel__num">Ball</span>
        <span className="shots-panel__num">Club</span>
        <span className="shots-panel__num">Launch</span>
        <span className="shots-panel__num">Spin</span>
        <span className="shots-panel__num">Carry</span>
        <span />
      </div>
      <div className="panel__body shots-panel__rows">
        {visibleShots.map((shot, index) => {
          const shotNumber = shots.length - index;
          const entry = entries[shot.timestamp] ?? getEmptyValidationEntry();
          const isOpen = expanded === shot.timestamp;
          const [ball, club, launch, spin, carry] = rowValues(shot, unitSystem);

          return (
            <div className="shots-panel__row-group" key={shot.timestamp}>
              <div className="shots-panel__row">
                <button
                  type="button"
                  className="shots-panel__row-main"
                  aria-expanded={isOpen}
                  onClick={() => setExpanded(isOpen ? null : shot.timestamp)}
                >
                  <span className="shots-panel__index">{shotNumber}</span>
                  <span className="shots-panel__player">
                    <span className="shots-panel__player-name">{shot.player_name ?? 'Player 1'}</span>
                    <span className="shots-panel__player-club">{shot.training_implement_label ?? shot.club}</span>
                  </span>
                  <span className="shots-panel__num shots-panel__value">{ball}</span>
                  <span className="shots-panel__num shots-panel__value">{club}</span>
                  <span className="shots-panel__num shots-panel__value">{launch}</span>
                  <span className="shots-panel__num shots-panel__value">{spin}</span>
                  <span className="shots-panel__num shots-panel__value shots-panel__value--accent">{carry}</span>
                </button>
                <button
                  type="button"
                  className="shots-panel__delete"
                  aria-label={`Delete shot ${shotNumber}`}
                  onClick={() => handleDelete(shot.timestamp)}
                >
                  Del
                </button>
              </div>
              {isOpen ? <ValidationEditor shot={shot} entry={entry} onUpdate={updateEntry} /> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
