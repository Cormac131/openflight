import type { SpinQuality } from '../../types/shot';
import './MetricCard.css';

export function MetricCard({
  value,
  unit,
  label,
  subtext,
  variant = 'default',
  size = 'standard',
  confidence,
}: {
  value: string | number;
  unit?: string;
  label: string;
  subtext?: string;
  variant?: 'default' | 'emphasis';
  size?: 'standard' | 'hero';
  confidence?: SpinQuality | null;
}) {
  const classes = ['metric-card', `metric-card--${variant}`];
  if (size === 'hero') {
    classes.push('metric-card--hero');
  }

  return (
    <div className={classes.join(' ')}>
      <div className="metric-card__value-row">
        <span className="metric-card__value">{value}</span>
        {unit ? <span className="metric-card__unit">{unit}</span> : null}
      </div>
      <span className="metric-card__label">{label}</span>
      {subtext ? <span className="metric-card__subtext">{subtext}</span> : null}
      {confidence ? (
        <div className={`metric-card__confidence metric-card__confidence--${confidence}`}>
          {confidence !== 'experimental' ? (
            <span className="metric-card__confidence-dots">
              <span className="dot filled" />
              <span className={`dot ${confidence === 'medium' || confidence === 'high' ? 'filled' : ''}`} />
              <span className={`dot ${confidence === 'high' ? 'filled' : ''}`} />
            </span>
          ) : null}
          <span className="metric-card__confidence-label">{confidence}</span>
        </div>
      ) : null}
    </div>
  );
}
