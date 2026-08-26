import { LOCALES, type LocaleId } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import { useSystemStore } from '../../stores/useSystemStore';
import { useCameraStore } from '../../stores/useCameraStore';
import { useThemeStore } from '../../stores/useThemeStore';
import { useLocaleStore } from '../../stores/useLocaleStore';
import { isLiveViewDurationMs, useLiveViewStore } from '../../stores/useLiveViewStore';
import { useUnitPreference } from '../../state/useUnitPreference';
import { socketService } from '../../services/socketService';
import { ballDetectionStatusLabel } from '../../utils/ballDetectionStatus';
import { SegmentedControl } from '../ui/SegmentedControl';
import { SimStatus } from '../SimStatus';

interface MenuSheetProps {
  onClose: () => void;
}

/**
 * The sheet behind the footer logo button (design doc 6a `menuOpen6`).
 *
 * 6a draws Units / Shut down. Players live on their own panel. The System
 * block is an addition: the mockup replaced the old top header, and simulator
 * and ball-detection state had nowhere else to go. Battery lives in the footer.
 * Socket connection lives on the panel header LED. Shutdown is footer power.
 */
export function MenuSheet({ onClose }: MenuSheetProps) {
  const simStatuses = useSystemStore((state) => state.simStatuses);
  const cameraStatus = useCameraStore((state) => state.cameraStatus);
  const { t } = useI18n();
  const { unitSystem, setUnitSystem } = useUnitPreference();
  const { theme, setTheme } = useThemeStore();
  const { locale, setLocale } = useLocaleStore();
  useLiveViewStore((state) => state.mode);
  useLiveViewStore((state) => state.durationMs);
  const { mode, durationMs, setMode, setDurationMs } = useLiveViewStore.getState();

  const ballDetectionValue = ballDetectionStatusLabel(cameraStatus);

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
          <span className="menu-sheet__section-title">{t('menu.liveView')}</span>
          <SegmentedControl
            ariaLabel={t('menu.liveView')}
            value={mode}
            options={[
              { id: 'tiles', label: t('onboarding.liveTiles') },
              { id: 'timed', label: t('onboarding.liveTimed') },
              { id: 'sticky', label: t('onboarding.liveHold') },
            ]}
            onChange={setMode}
          />
          {mode === 'timed' ? (
            <SegmentedControl
              ariaLabel={t('onboarding.duration')}
              value={String(durationMs)}
              options={[
                { id: '5000', label: t('onboarding.duration5') },
                { id: '10000', label: t('onboarding.duration10') },
                { id: '15000', label: t('onboarding.duration15') },
              ]}
              onChange={(id) => {
                const next = Number(id);
                if (isLiveViewDurationMs(next)) {
                  setDurationMs(next);
                }
              }}
            />
          ) : null}
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
      </div>
    </>
  );
}
