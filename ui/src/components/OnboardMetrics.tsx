import { useI18n } from '../i18n/useI18n';
import type { MessageKey } from '../i18n/useI18n';
import type { IWROnboardMetric, IWROnboardMetricName, IWROnboardResult } from '../types/shot';
import './OnboardMetrics.css';

const MPS_TO_MPH = 2.23694;

interface Row {
  name: IWROnboardMetricName;
  label: MessageKey;
  format: (value: number) => string;
  unit: string;
}

const ROWS: Row[] = [
  { name: 'ball_speed', label: 'onboard.ball', format: (v) => (v * MPS_TO_MPH).toFixed(1), unit: 'mph' },
  { name: 'vertical_launch', label: 'onboard.vla', format: (v) => v.toFixed(1), unit: '°' },
  { name: 'horizontal_launch', label: 'onboard.hla', format: (v) => v.toFixed(1), unit: '°' },
  { name: 'club_speed', label: 'onboard.club', format: (v) => (v * MPS_TO_MPH).toFixed(1), unit: 'mph' },
  { name: 'club_path', label: 'onboard.path', format: (v) => v.toFixed(1), unit: '°' },
  { name: 'angle_of_attack', label: 'onboard.attack', format: (v) => v.toFixed(1), unit: '°' },
];

const VERDICT_LABEL: Record<IWROnboardResult['verdict'], MessageKey> = {
  valid: 'onboard.verdictValid',
  partial: 'onboard.verdictPartial',
  invalid: 'onboard.verdictInvalid',
};

/** Display value: the number, or a dash for invalid or implausible metrics. */
function formatOnboardValue(metric: IWROnboardMetric | undefined, row: Row): string {
  if (!metric || !metric.usable || metric.value === null) return '—';
  return row.format(metric.value);
}

interface OnboardMetricsProps {
  result: IWROnboardResult | null | undefined;
}

/**
 * The TI firmware's own shot result beside the host pipeline's: every metric
 * carries its MEASURED / ESTIMATED provenance and confidence so the two are
 * never blurred. Renders nothing when the shot has no onboard result.
 */
export function OnboardMetrics({ result }: OnboardMetricsProps) {
  const { t } = useI18n();
  if (!result) {
    return null;
  }
  return (
    <section className={`onboard-metrics onboard-metrics--${result.verdict}`} aria-label={t('onboard.title')}>
      <header className="onboard-metrics__header">
        <span className="onboard-metrics__title">{t('onboard.title')}</span>
        <span className="onboard-metrics__verdict">{t(VERDICT_LABEL[result.verdict])}</span>
        <span className="onboard-metrics__meta">
          {t('onboard.shot', { n: String(result.shot_id) })} ·{' '}
          {t('onboard.points', { club: String(result.club_points), ball: String(result.ball_points) })}
          {result.smash !== null ? ` · ${t('onboard.smash')} ${result.smash.toFixed(2)}` : ''}
        </span>
      </header>
      <div className="onboard-metrics__grid">
        {ROWS.map((row) => {
          const metric = result.metrics[row.name];
          const value = formatOnboardValue(metric, row);
          const usable = value !== '—';
          const tag = metric?.measured ? t('onboard.measured') : t('onboard.estimated');
          return (
            <div
              key={row.name}
              className={`onboard-metrics__item ${usable ? '' : 'onboard-metrics__item--empty'}`}
              title={metric?.radial_only ? 'radial only' : undefined}
            >
              <span className="onboard-metrics__label">{t(row.label)}</span>
              <span className="onboard-metrics__value">
                {value}
                {usable ? <span className="onboard-metrics__unit">{row.unit}</span> : null}
              </span>
              {usable && metric ? (
                <span
                  className={`onboard-metrics__tag onboard-metrics__tag--${metric.measured ? 'measured' : 'estimated'}`}
                >
                  {tag} {Math.round(metric.confidence * 100)}%
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}
