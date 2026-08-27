import { useI18n } from '../i18n/useI18n';
import type { HardwareFault } from '../types/hardware';
import './HardwareFaultBanner.css';

interface HardwareFaultBannerProps {
  faults: HardwareFault[];
  onDismiss: (device: string) => void;
}

/**
 * Dismissible strip for hardware that failed but left a usable session.
 *
 * An IWR6843 that will not initialise costs launch angle and club path; the
 * owner can still hit balls and read ball speed. Covering the screen for that
 * would be worse than saying nothing, so these stack above the app instead —
 * and stay dismissible, because somebody who knows their second angle radar
 * is unplugged should not have to look at it all session.
 */
export function HardwareFaultBanner({ faults, onDismiss }: HardwareFaultBannerProps) {
  const { t } = useI18n();

  if (faults.length === 0) return null;

  return (
    <>
      {faults.map((fault) => (
        <div className="hardware-banner" role="status" key={fault.device}>
          <div className="hardware-banner__text">
            <strong>{fault.title}</strong>
            <span>{fault.remedy}</span>
          </div>
          <button
            type="button"
            onClick={() => onDismiss(fault.device)}
            aria-label={t('hardware.dismissFault', { device: fault.title })}
          >
            {t('live.dismiss')}
          </button>
        </div>
      ))}
    </>
  );
}
