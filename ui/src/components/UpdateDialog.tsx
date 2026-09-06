import { useI18n } from '../i18n/useI18n';
import { ActionDialog } from './ui/ActionDialog';

/** `blocked` is the server's "busy" answer: a shot is in flight or just happened. */
export type UpdateDialogState = 'confirm' | 'pending' | 'blocked' | 'error';

interface UpdateDialogProps {
  state: UpdateDialogState;
  stagedName: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

export function UpdateDialog({ state, stagedName, onConfirm, onCancel }: UpdateDialogProps) {
  const { t } = useI18n();
  const blocked = state === 'blocked';

  return (
    <ActionDialog
      state={blocked ? 'error' : state}
      titleId="update-dialog-title"
      labels={{
        confirm: t('update.confirm', { tag: stagedName ?? '' }),
        confirmDetail: t('update.confirmDetail'),
        action: t('update.restart'),
        cancel: t('update.cancel'),
        pendingAria: t('update.pendingAria'),
        pendingTitle: t('update.pendingTitle'),
        pendingDetail: t('update.pendingDetail'),
        error: blocked ? t('update.blocked') : t('update.error'),
        errorDetail: blocked ? t('update.blockedDetail') : t('update.errorDetail'),
        tryAgain: t('update.tryAgain'),
      }}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
