import { LOCALES, type LocaleId } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import { useSystemStore } from '../../stores/useSystemStore';
import { useCameraStore } from '../../stores/useCameraStore';
import { useThemeStore } from '../../stores/useThemeStore';
import { useLocaleStore } from '../../stores/useLocaleStore';
import { useUnitPreference } from '../../state/useUnitPreference';
import { useUpdateStore } from '../../stores/useUpdateStore';
import { socketService } from '../../services/socketService';
import { ballDetectionStatusLabel } from '../../utils/ballDetectionStatus';
import { SegmentedControl } from '../ui/SegmentedControl';
import { SimStatus } from '../SimStatus';

interface MenuSheetProps {
  onClose: () => void;
  onShutdown: () => void;
  onApplyUpdate: () => void;
}

/**
 * The sheet behind the footer logo button (design doc 6a `menuOpen6`).
 *
 * 6a draws Units / Shut down. Profiles live on their own panel. The System
 * block is an addition: the mockup replaced the old top header, and simulator
 * and ball-detection state had nowhere else to go. Battery lives in the footer.
 * Socket connection lives on the panel header LED.
 */
export function MenuSheet({ onClose, onShutdown, onApplyUpdate }: MenuSheetProps) {
  const simStatuses = useSystemStore((state) => state.simStatuses);
  const cameraStatus = useCameraStore((state) => state.cameraStatus);
  const { t } = useI18n();
  const { unitSystem, setUnitSystem } = useUnitPreference();
  const { theme, setTheme } = useThemeStore();
  const { locale, setLocale } = useLocaleStore();
  const { status: updateStatus, checkForUpdate, channel, setChannel } = useUpdateStore();

  const ballDetectionValue = ballDetectionStatusLabel(cameraStatus);

  // Derive a short status label and available actions for the updates row.
  const isElectron = typeof window !== 'undefined' && 'electronUpdate' in window;
  const updateStatusLabel = (() => {
    if (!isElectron) return null;
    switch (updateStatus.type) {
      case 'checking': return t('menu.updateChecking');
      case 'upToDate': return t('menu.upToDate');
      case 'available': return t('menu.updateAvailable');
      case 'applying': return t('menu.updateApplying');
      case 'buildFailed':
      case 'error': return t('menu.updateError');
      default: return null;
    }
  })();

  return (
    <>
      <button type="button" className="panel-scrim" onClick={onClose} aria-label={t('menu.close')} />
      <div className="menu-sheet" role="dialog" aria-modal="true" aria-label={t('menu.title')}>
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

        {isElectron ? (
          <section className="menu-sheet__section">
            <span className="menu-sheet__section-title">{t('menu.updates')}</span>
            <div className="menu-sheet__status-row">
              {updateStatusLabel ? (
                <span className="menu-sheet__status-value">{updateStatusLabel}</span>
              ) : null}
              {updateStatus.type === 'available' ? (
                <button
                  type="button"
                  className="menu-sheet__chip menu-sheet__chip--accent"
                  onClick={() => {
                    onClose();
                    onApplyUpdate();
                  }}
                >
                  {t('menu.applyUpdate')}
                </button>
              ) : null}
              {(updateStatus.type === 'idle' ||
                updateStatus.type === 'upToDate' ||
                updateStatus.type === 'error') ? (
                <button
                  type="button"
                  className="menu-sheet__chip"
                  onClick={() => checkForUpdate()}
                >
                  {t('menu.checkForUpdates')}
                </button>
              ) : null}
            </div>
            <div className="menu-sheet__status-row">
              <span className="menu-sheet__status-label">{t('menu.updateChannel')}</span>
              <SegmentedControl
                ariaLabel={t('menu.updateChannel')}
                value={channel}
                options={[
                  { id: 'stable', label: t('menu.channelStable') },
                  { id: 'experimental', label: t('menu.channelExperimental') },
                ]}
                onChange={(ch) => setChannel(ch as 'stable' | 'experimental')}
              />
            </div>
          </section>
        ) : null}

        <button type="button" className="menu-sheet__shutdown" onClick={onShutdown}>
          {t('menu.shutdown')}
        </button>
      </div>
    </>
  );
}
