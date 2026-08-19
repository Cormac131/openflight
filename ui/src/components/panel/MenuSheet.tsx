import { useState } from 'react';
import { usePlayerStore } from '../../stores/usePlayerStore';
import { useSystemStore } from '../../stores/useSystemStore';
import { useCameraStore } from '../../stores/useCameraStore';
import { useThemeStore } from '../../stores/useThemeStore';
import { useUnitPreference } from '../../state/useUnitPreference';
import { socketService } from '../../services/socketService';
import { SegmentedControl } from '../ui/SegmentedControl';
import { PowerExperience } from '../PowerStatus';
import { SimStatus } from '../SimStatus';

interface MenuSheetProps {
  onClose: () => void;
  onShutdown: () => void;
}

/**
 * The sheet behind the footer logo button (design doc 6a `menuOpen6`).
 *
 * 6a draws Player / Units / Shut down. The System block is an addition: the
 * mockup replaced the old top header, and battery, simulator and
 * ball-detection state had nowhere else to go. Socket connection lives on the
 * panel header LED.
 */
export function MenuSheet({ onClose, onShutdown }: MenuSheetProps) {
  const [newPlayer, setNewPlayer] = useState('');
  const { players, selectedPlayer, addPlayer, removePlayer, selectPlayer } = usePlayerStore();
  const simStatuses = useSystemStore((state) => state.simStatuses);
  const cameraStatus = useCameraStore((state) => state.cameraStatus);
  const { unitSystem, setUnitSystem } = useUnitPreference();
  const { theme, setTheme } = useThemeStore();

  const handleSelectPlayer = (playerName: string) => {
    selectPlayer(playerName);
    socketService.setPlayer(playerName);
  };

  const handleAddPlayer = () => {
    if (!newPlayer.trim()) return;
    const playerName = addPlayer(newPlayer);
    socketService.setPlayer(playerName);
    setNewPlayer('');
  };

  const handleRemovePlayer = (playerName: string) => {
    removePlayer(playerName);
    if (playerName === selectedPlayer) {
      socketService.setPlayer(players.find((player) => player !== playerName) ?? 'Player 1');
    }
  };

  const ballDetectionValue = !cameraStatus.available
    ? 'Unavailable'
    : !cameraStatus.enabled
      ? 'Off'
      : cameraStatus.ball_detected
        ? `Ball ${Math.round(cameraStatus.ball_confidence * 100)}%`
        : 'Searching';

  return (
    <>
      <button type="button" className="panel-scrim" onClick={onClose} aria-label="Close menu" />
      <div className="menu-sheet" role="dialog" aria-modal="true" aria-label="Menu">
        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">Player</span>
          <div className="menu-sheet__chips">
            {players.map((playerName) => (
              <span className="menu-sheet__chip-group" key={playerName}>
                <button
                  type="button"
                  className={`menu-sheet__chip${playerName === selectedPlayer ? ' menu-sheet__chip--active' : ''}`}
                  aria-pressed={playerName === selectedPlayer}
                  onClick={() => handleSelectPlayer(playerName)}
                >
                  {playerName}
                </button>
                {players.length > 1 ? (
                  <button
                    type="button"
                    className="menu-sheet__chip-remove"
                    aria-label={`Remove ${playerName}`}
                    onClick={() => handleRemovePlayer(playerName)}
                  >
                    ✕
                  </button>
                ) : null}
              </span>
            ))}
          </div>
          <div className="menu-sheet__add-row">
            <input
              className="menu-sheet__input"
              type="text"
              placeholder="Add player"
              maxLength={40}
              value={newPlayer}
              onChange={(event) => setNewPlayer(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') handleAddPlayer();
              }}
            />
            <button type="button" className="menu-sheet__chip" onClick={handleAddPlayer}>
              Add
            </button>
          </div>
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">Units</span>
          <SegmentedControl
            ariaLabel="Display units"
            value={unitSystem}
            options={[
              { id: 'imperial', label: 'MPH / YDS' },
              { id: 'metric', label: 'KMH / M' },
            ]}
            onChange={setUnitSystem}
          />
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">Theme</span>
          <SegmentedControl
            ariaLabel="Theme"
            value={theme}
            options={[
              { id: 'dark', label: 'Dark' },
              { id: 'light', label: 'Light' },
            ]}
            onChange={setTheme}
          />
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">System</span>
          <div className="menu-sheet__status-row">
            <span className="menu-sheet__status-label">Battery</span>
            <PowerExperience />
          </div>
          <div className="menu-sheet__status-row">
            <span className="menu-sheet__status-label">Ball detection</span>
            <span className="menu-sheet__status-value">{ballDetectionValue}</span>
            {cameraStatus.available ? (
              <button type="button" className="menu-sheet__chip" onClick={() => socketService.toggleCamera()}>
                {cameraStatus.enabled ? 'Disable' : 'Enable'}
              </button>
            ) : null}
          </div>
          {Object.keys(simStatuses).length > 0 ? (
            <div className="menu-sheet__status-row">
              <span className="menu-sheet__status-label">Simulators</span>
              <SimStatus statuses={simStatuses} />
            </div>
          ) : null}
        </section>

        <button type="button" className="menu-sheet__shutdown" onClick={onShutdown}>
          Shut down
        </button>
      </div>
    </>
  );
}
