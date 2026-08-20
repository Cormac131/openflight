import { useState } from 'react';
import { LOCALES, type LocaleId } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import { usePlayerStore } from '../../stores/usePlayerStore';
import { useSystemStore } from '../../stores/useSystemStore';
import { useCameraStore } from '../../stores/useCameraStore';
import { useThemeStore } from '../../stores/useThemeStore';
import { useLocaleStore } from '../../stores/useLocaleStore';
import { useUnitPreference } from '../../state/useUnitPreference';
import { socketService } from '../../services/socketService';
import { SegmentedControl } from '../ui/SegmentedControl';
import { PowerExperience } from '../PowerStatus';
import { SimStatus } from '../SimStatus';
import type { PowerStatus } from '../../types/power';

interface MenuSheetProps {
  onClose: () => void;
  onShutdown: () => void;
  /**
   * Battery telemetry. Omit to read `useSystemStore`; pass it in tests so SSR
   * is not stuck with the store's initial `null`.
   */
  powerStatus?: PowerStatus | null;
}

/**
 * The sheet behind the footer logo button (design doc 6a `menuOpen6`).
 *
 * 6a draws Player / Units / Shut down. The System block is an addition: the
 * mockup replaced the old top header, and battery, simulator and
 * ball-detection state had nowhere else to go. Socket connection lives on the
 * panel header LED.
 */
export function MenuSheet({ onClose, onShutdown, powerStatus: powerStatusProp }: MenuSheetProps) {
  const [newPlayer, setNewPlayer] = useState('');
  const { players, selectedPlayer, addPlayer, removePlayer, selectPlayer } = usePlayerStore();
  const storePowerStatus = useSystemStore((state) => state.powerStatus);
  const powerStatus = powerStatusProp ?? storePowerStatus;
  const simStatuses = useSystemStore((state) => state.simStatuses);
  const cameraStatus = useCameraStore((state) => state.cameraStatus);
  const { t } = useI18n();
  const { unitSystem, setUnitSystem } = useUnitPreference();
  const { theme, setTheme } = useThemeStore();
  const { locale, setLocale } = useLocaleStore();

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
    ? t('menu.unavailable')
    : !cameraStatus.enabled
      ? t('menu.off')
      : cameraStatus.ball_detected
        ? t('menu.ballPercent', { percent: Math.round(cameraStatus.ball_confidence * 100) })
        : t('menu.searching');

  return (
    <>
      <button type="button" className="panel-scrim" onClick={onClose} aria-label={t('menu.close')} />
      <div className="menu-sheet" role="dialog" aria-modal="true" aria-label={t('menu.title')}>
        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.player')}</span>
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
                    aria-label={t('menu.removePlayer', { name: playerName })}
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
              placeholder={t('menu.addPlayer')}
              maxLength={40}
              value={newPlayer}
              onChange={(event) => setNewPlayer(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') handleAddPlayer();
              }}
            />
            <button type="button" className="menu-sheet__chip" onClick={handleAddPlayer}>
              {t('menu.add')}
            </button>
          </div>
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.units')}</span>
          <SegmentedControl
            ariaLabel={t('menu.displayUnits')}
            value={unitSystem}
            options={[
              { id: 'imperial', label: 'MPH / YDS' },
              { id: 'metric', label: 'KMH / M' },
            ]}
            onChange={setUnitSystem}
          />
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.theme')}</span>
          <SegmentedControl
            ariaLabel={t('menu.theme')}
            value={theme}
            options={[
              { id: 'dark', label: t('menu.themeDark') },
              { id: 'light', label: t('menu.themeLight') },
            ]}
            onChange={setTheme}
          />
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.language')}</span>
          <select
            className="menu-sheet__select"
            aria-label={t('menu.language')}
            value={locale}
            onChange={(event) => setLocale(event.target.value as LocaleId)}
          >
            {LOCALES.map((option) => (
              <option key={option.id} value={option.id}>
                {option.nativeName}
              </option>
            ))}
          </select>
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.system')}</span>
          {powerStatus ? (
            <div className="menu-sheet__status-row">
              <span className="menu-sheet__status-label">{t('menu.battery')}</span>
              <PowerExperience status={powerStatus} />
            </div>
          ) : null}
          <div className="menu-sheet__status-row">
            <span className="menu-sheet__status-label">{t('menu.ballDetection')}</span>
            <span className="menu-sheet__status-value">{ballDetectionValue}</span>
            {cameraStatus.available ? (
              <button type="button" className="menu-sheet__chip" onClick={() => socketService.toggleCamera()}>
                {cameraStatus.enabled ? t('menu.disable') : t('menu.enable')}
              </button>
            ) : null}
          </div>
          {Object.keys(simStatuses).length > 0 ? (
            <div className="menu-sheet__status-row">
              <span className="menu-sheet__status-label">{t('menu.simulators')}</span>
              <SimStatus statuses={simStatuses} />
            </div>
          ) : null}
        </section>

        <button type="button" className="menu-sheet__shutdown" onClick={onShutdown}>
          {t('menu.shutdown')}
        </button>
      </div>
    </>
  );
}
