import { ProgressIndicator } from './ProgressIndicator';
import { useI18n } from '../i18n/useI18n';

export type UpdateDialogState = 'prompt' | 'confirmActiveSession' | 'pending' | 'error';

interface UpdateAvailableDialogProps {
  state: UpdateDialogState;
  tag: string;
  notes: string;
  /** "Apply now" clicked from the main prompt — may route to the
   * active-session confirm step rather than applying immediately. */
  onApplyNowClick: () => void;
  /** The action itself: apply immediately, no further confirmation. Used by
   * the active-session confirm step and by "Try Again" after an error. */
  onApplyConfirmed: () => void;
  onCancelConfirm: () => void;
  onNextRestart: () => void;
  onNever: () => void;
}

export function UpdateAvailableDialog({
  state,
  tag,
  notes,
  onApplyNowClick,
  onApplyConfirmed,
  onCancelConfirm,
  onNextRestart,
  onNever,
}: UpdateAvailableDialogProps) {
  const { t } = useI18n();

  if (state === 'pending') {
    return (
      <div className="shutdown-overlay">
        <div
          className="update-dialog update-dialog--pending"
          role="dialog"
          aria-modal="true"
          aria-label={t('update.pendingAria')}
        >
          <ProgressIndicator variant="dialog" title={t('update.pendingTitle')} detail={t('update.pendingDetail')} />
        </div>
      </div>
    );
  }

  if (state === 'confirmActiveSession') {
    return (
      <div className="shutdown-overlay">
        <div className="update-dialog" role="dialog" aria-modal="true" aria-labelledby="update-dialog-title">
          <p id="update-dialog-title">{t('update.confirmActiveSessionTitle')}</p>
          <span className="update-dialog__detail">{t('update.confirmActiveSessionDetail')}</span>
          <div className="update-dialog__buttons">
            <button className="update-dialog__confirm" onClick={onApplyConfirmed} autoFocus>
              {t('update.applyNow')}
            </button>
            <button className="update-dialog__cancel" onClick={onCancelConfirm}>
              {t('update.cancel')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const hasError = state === 'error';
  return (
    <div className="shutdown-overlay">
      <div className="update-dialog" role="dialog" aria-modal="true" aria-labelledby="update-dialog-title">
        <p id="update-dialog-title">{hasError ? t('update.error') : t('update.available', { tag })}</p>
        {hasError ? (
          <span className="update-dialog__detail update-dialog__detail--error">{t('update.errorDetail')}</span>
        ) : (
          notes && <span className="update-dialog__detail">{notes}</span>
        )}
        <div className="update-dialog__buttons">
          <button className="update-dialog__confirm" onClick={hasError ? onApplyConfirmed : onApplyNowClick} autoFocus>
            {hasError ? t('update.tryAgain') : t('update.applyNow')}
          </button>
          {!hasError && (
            <button className="update-dialog__secondary" onClick={onNextRestart}>
              {t('update.nextRestart')}
            </button>
          )}
          <button className="update-dialog__cancel" onClick={onNever}>
            {t('update.never')}
          </button>
        </div>
      </div>
    </div>
  );
}
