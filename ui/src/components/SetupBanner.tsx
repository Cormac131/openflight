import { useI18n } from '../i18n/useI18n';
import type { IWRSetupStatus } from '../types/shot';
import './SetupBanner.css';

type Tone = 'ok' | 'warn' | 'error';

function tone(status: IWRSetupStatus): Tone {
  if (status.state === 'error') return 'error';
  if (status.state === 'locked') {
    if (status.label === 'ideal') return 'ok';
    return status.ok ? 'warn' : 'error';
  }
  return 'warn';
}

interface SetupBannerProps {
  status: IWRSetupStatus | null;
}

/**
 * One-line placement guidance from the TI radar's ball detector: where the
 * ball is seen and how far to move OpenFlight. Nothing renders while the
 * detector is off so the Live tiles keep their height on rigs without it.
 */
export function SetupBanner({ status }: SetupBannerProps) {
  const { t } = useI18n();
  if (!status || !status.enabled || status.state === 'off') {
    return null;
  }
  const severity = tone(status);
  let headline: string;
  let detail: string | null = null;
  if (status.state === 'locked' && status.range_m !== null) {
    headline = t('setup.ballAt', { range: status.range_m.toFixed(2) });
    const cm = String(Math.abs(status.move_cm ?? 0));
    const direction = (status.move_cm ?? 0) > 0 ? t('setup.closer') : t('setup.back');
    if (status.label === 'ideal') {
      detail = t('setup.ready');
    } else if (status.ok) {
      detail = t('setup.usableMove', { cm, direction });
    } else {
      detail = t('setup.move', { cm, direction });
    }
  } else if (status.state === 'building') {
    headline = t('setup.building');
  } else if (status.state === 'candidate') {
    headline = t('setup.candidate');
  } else if (status.state === 'error') {
    headline = t('setup.error');
    detail = status.error;
  } else {
    headline = t('setup.waiting');
  }
  return (
    <div className={`setup-banner setup-banner--${severity}`} role="status" aria-label={t('setup.title')}>
      <span className="setup-banner__dot" />
      <strong className="setup-banner__headline">{headline}</strong>
      {detail ? <span className="setup-banner__detail">{detail}</span> : null}
    </div>
  );
}
