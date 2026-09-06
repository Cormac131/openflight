import { useI18n } from '../../i18n/useI18n';
import type { UpdateStatus } from '../../types/update';
import { SegmentedControl } from '../ui/SegmentedControl';
import {
  updateChannelValue,
  updateInProgress,
  updateStatusLabel,
  type UpdateChannelValue,
} from '../../utils/updateLabel';

interface UpdatesSectionProps {
  status: UpdateStatus | null;
  onSetChannel: (channel: UpdateChannelValue) => void;
  onCheck: () => void;
  onRestartToUpdate: () => void;
}

/**
 * The menu's Updates block. Presentational so it can be rendered with any
 * status in tests; MenuSheet feeds it the store value and the socket calls.
 */
export function UpdatesSection({ status, onSetChannel, onCheck, onRestartToUpdate }: UpdatesSectionProps) {
  const { t } = useI18n();
  const managed = status?.managed === true;
  const canCheck = managed && status?.channel !== null && !updateInProgress(status);
  const canRestart = status?.state === 'staged';

  return (
    <section className="menu-sheet__section">
      <span className="menu-sheet__section-title">{t('menu.updates')}</span>
      {managed ? (
        <SegmentedControl
          ariaLabel={t('menu.updateChannel')}
          value={updateChannelValue(status)}
          options={[
            { id: 'stable', label: t('menu.channelStable') },
            { id: 'experimental', label: t('menu.channelExperimental') },
            { id: 'off', label: t('menu.off') },
          ]}
          onChange={onSetChannel}
        />
      ) : null}
      <div className="menu-sheet__status-row">
        <span className="menu-sheet__status-label">{t('menu.updateStatus')}</span>
        <span className="menu-sheet__status-value">{updateStatusLabel(status, t)}</span>
        {canCheck ? (
          <button type="button" className="menu-sheet__chip" onClick={onCheck}>
            {t('menu.checkNow')}
          </button>
        ) : null}
        {canRestart ? (
          <button type="button" className="menu-sheet__chip menu-sheet__chip--active" onClick={onRestartToUpdate}>
            {t('menu.restartToUpdate')}
          </button>
        ) : null}
      </div>
    </section>
  );
}
