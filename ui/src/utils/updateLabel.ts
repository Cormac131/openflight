import type { MessageKey } from '../i18n/useI18n';
import type { UpdateChannel, UpdateStatus } from '../types/update';

export type UpdateChannelValue = UpdateChannel | 'off';

type Translate = (key: MessageKey, params?: Record<string, string | number>) => string;

const BUSY_STATES: ReadonlySet<UpdateStatus['state']> = new Set(['checking', 'downloading', 'staging', 'restarting']);

/** The channel control's value: `off` while no channel is followed. */
export function updateChannelValue(status: UpdateStatus | null): UpdateChannelValue {
  return status?.channel ?? 'off';
}

/** True while the updater is working, so the kiosk hides "Check now". */
export function updateInProgress(status: UpdateStatus | null): boolean {
  return status !== null && BUSY_STATES.has(status.state);
}

/** One-line status for the menu's Updates row. */
export function updateStatusLabel(status: UpdateStatus | null, t: Translate): string {
  if (!status) {
    return t('menu.unavailable');
  }
  const tag = status.available?.tag ?? status.staged?.name ?? '';
  switch (status.state) {
    case 'unmanaged':
      return t('update.unmanaged');
    case 'disabled':
      return t('update.disabled');
    case 'checking':
      return t('update.checking');
    case 'downloading':
      return t('update.downloading', { tag });
    case 'staging':
      return t('update.staging', { tag });
    case 'staged':
      return t('update.staged', { tag: status.staged?.name ?? tag });
    case 'pending_confirm':
      return t('update.pendingConfirm', { version: status.current.version });
    case 'up_to_date':
      return t('update.upToDate');
    case 'held_back':
      return t('update.heldBack', { tag });
    case 'failed':
      return t('update.failed', { error: status.error ?? '' });
    case 'restarting':
      return t('update.restarting');
    default:
      return t('menu.unavailable');
  }
}
