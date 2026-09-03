import { ProgressIndicator } from './ProgressIndicator';
import { useI18n } from '../i18n/useI18n';
import type { UpdateStatusType, ApplyStage } from '../types/electronUpdate';

interface UpdateDialogProps {
  status: UpdateStatusType;
  onDismiss: () => void;
}

const STAGE_KEYS: Record<ApplyStage, 'update.stageDownloading' | 'update.stageVerifying' | 'update.stageExtracting' | 'update.stageInstalling' | 'update.stageSwapping'> = {
  downloading: 'update.stageDownloading',
  verifying: 'update.stageVerifying',
  extracting: 'update.stageExtracting',
  installing: 'update.stageInstalling',
  swapping: 'update.stageSwapping',
};

/** Full-screen overlay shown while an update is being applied or has failed. */
export function UpdateDialog({ status, onDismiss }: UpdateDialogProps) {
  const { t } = useI18n();

  if (status.type === 'applying') {
    const stageKey = STAGE_KEYS[status.stage];
    return (
      <div className="shutdown-overlay">
        <div
          className="shutdown-dialog shutdown-dialog--pending"
          role="dialog"
          aria-modal="true"
          aria-label={t('update.applyAria')}
        >
          <ProgressIndicator
            variant="dialog"
            title={t('update.applyingTitle')}
            detail={stageKey ? t(stageKey) : undefined}
          />
        </div>
      </div>
    );
  }

  if (status.type === 'ready' || status.type === 'restarting') {
    return (
      <div className="shutdown-overlay">
        <div
          className="shutdown-dialog shutdown-dialog--pending"
          role="dialog"
          aria-modal="true"
          aria-label={t('update.applyAria')}
        >
          <ProgressIndicator variant="dialog" title={t('update.restartingTitle')} />
        </div>
      </div>
    );
  }

  if (status.type === 'buildFailed') {
    return (
      <div className="shutdown-overlay">
        <div
          className="shutdown-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="update-dialog-title"
        >
          <p id="update-dialog-title">{t('update.buildFailedTitle')}</p>
          <span className="shutdown-dialog__error">{t('update.buildFailedDetail')}</span>
          <div className="shutdown-dialog__buttons">
            <button className="shutdown-dialog__cancel" onClick={onDismiss}>
              {t('update.dismiss')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return null;
}
