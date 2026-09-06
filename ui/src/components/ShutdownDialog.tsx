import { useI18n } from '../i18n/useI18n';
import { ActionDialog, type ActionDialogState } from './ui/ActionDialog';

export type ShutdownState = ActionDialogState;

interface ShutdownDialogProps {
  state: ShutdownState;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ShutdownDialog({ state, onConfirm, onCancel }: ShutdownDialogProps) {
  const { t } = useI18n();

  return (
    <ActionDialog
      state={state}
      titleId="shutdown-dialog-title"
      labels={{
        confirm: t('shutdown.confirm'),
        action: t('shutdown.shutDown'),
        cancel: t('shutdown.cancel'),
        pendingAria: t('shutdown.pendingAria'),
        pendingTitle: t('shutdown.pendingTitle'),
        pendingDetail: t('shutdown.pendingDetail'),
        error: t('shutdown.error'),
        errorDetail: t('shutdown.errorDetail'),
        tryAgain: t('shutdown.tryAgain'),
      }}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
