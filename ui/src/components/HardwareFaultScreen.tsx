import { useI18n } from '../i18n/useI18n';
import type { HardwareFault } from '../types/hardware';
import './HardwareFaultScreen.css';

interface HardwareFaultScreenProps {
  fault: HardwareFault;
  /** Whether the socket is up. A dead socket changes what the owner should try. */
  serverConnected: boolean;
}

/**
 * Full-screen notice for a fault that makes the launch monitor unusable.
 *
 * This exists because the alternative was nothing. A missing radar used to
 * take the whole server down before it could serve a page, so the owner of a
 * keyboard-less kiosk got a blank screen and no way to find out why. The
 * server now stays up and sends the fault here.
 *
 * Deliberately not dismissible: there is no working product behind it, so a
 * dismiss button would only hide the explanation and leave an empty app.
 * Degraded faults, where shots still work, get a dismissible banner instead.
 */
export function HardwareFaultScreen({ fault, serverConnected }: HardwareFaultScreenProps) {
  const { t } = useI18n();

  return (
    <div className="hardware-fault" role="alert" aria-live="assertive">
      <div className="hardware-fault__card">
        <h1 className="hardware-fault__title">{fault.title}</h1>
        <p className="hardware-fault__remedy">{fault.remedy}</p>

        {!serverConnected && (
          <p className="hardware-fault__offline">{t('hardware.serverOffline')}</p>
        )}

        {fault.detail && (
          // Small and last: this is the line to quote in a support request,
          // not the line to act on.
          <p className="hardware-fault__detail">{fault.detail}</p>
        )}
      </div>
    </div>
  );
}
