/** Mirrors `compose_update_status()` in `src/openflight/update/status.py`. */

export type UpdateChannel = 'stable' | 'experimental';

export type UpdateState =
  | 'unmanaged'
  | 'disabled'
  | 'checking'
  | 'downloading'
  | 'staging'
  | 'staged'
  | 'pending_confirm'
  | 'up_to_date'
  | 'held_back'
  | 'failed'
  /** Added by the server only in the event it emits before exiting to restart. */
  | 'restarting';

export interface UpdateRelease {
  name: string;
  tag: string | null;
  version: string | null;
  channel: string | null;
}

export interface UpdateStatus {
  format_version: number;
  managed: boolean;
  state: UpdateState;
  channel: UpdateChannel | null;
  repository: string;
  current: { tag: string | null; version: string; channel: string };
  available: { tag: string; version: string; size: number } | null;
  staged: UpdateRelease | null;
  previous: UpdateRelease | null;
  pending_confirm: boolean;
  last_check_at: string | null;
  error: string | null;
}

export type UpdateAction = 'set_update_channel' | 'check_for_updates' | 'apply_update';

export type UpdateErrorReason =
  'forbidden' | 'invalid_channel' | 'throttled' | 'spawn_failed' | 'nothing_staged' | 'busy';

export interface UpdateError {
  action: UpdateAction;
  reason: UpdateErrorReason;
}
