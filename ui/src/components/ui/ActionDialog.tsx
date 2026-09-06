import { ProgressIndicator } from '../ProgressIndicator';

export type ActionDialogState = 'confirm' | 'pending' | 'error';

export interface ActionDialogLabels {
  confirm: string;
  confirmDetail?: string;
  action: string;
  cancel: string;
  pendingAria: string;
  pendingTitle: string;
  pendingDetail: string;
  error: string;
  errorDetail: string;
  tryAgain: string;
}

interface ActionDialogProps {
  state: ActionDialogState;
  titleId: string;
  labels: ActionDialogLabels;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * A modal confirm / pending / error flow for actions that take the kiosk
 * down (shut down, restart to update). The pending view has no controls on
 * purpose: the server is already acting and the window is about to go.
 */
export function ActionDialog({ state, titleId, labels, onConfirm, onCancel }: ActionDialogProps) {
  if (state === 'pending') {
    return (
      <div className="shutdown-overlay">
        <div
          className="shutdown-dialog shutdown-dialog--pending"
          role="dialog"
          aria-modal="true"
          aria-label={labels.pendingAria}
        >
          <ProgressIndicator variant="dialog" title={labels.pendingTitle} detail={labels.pendingDetail} />
        </div>
      </div>
    );
  }

  const hasError = state === 'error';
  const detail = hasError ? labels.errorDetail : labels.confirmDetail;
  return (
    <div className="shutdown-overlay">
      <div className="shutdown-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <p id={titleId}>{hasError ? labels.error : labels.confirm}</p>
        {detail ? <span className="shutdown-dialog__error">{detail}</span> : null}
        <div className="shutdown-dialog__buttons">
          <button className="shutdown-dialog__confirm" onClick={onConfirm} autoFocus>
            {hasError ? labels.tryAgain : labels.action}
          </button>
          <button className="shutdown-dialog__cancel" onClick={onCancel}>
            {labels.cancel}
          </button>
        </div>
      </div>
    </div>
  );
}
