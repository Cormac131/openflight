import { useI18n } from '../../i18n/useI18n';
import { getClubName } from '../../data/clubs';
import { PanelAction } from './PanelAction';
import { PickerOverlay } from './PickerOverlay';
import { clubSections } from './pickerSections';
import type { NfcScan, WriteStage } from '../../types/nfc';

interface ClubTagWriteFlowProps {
  scan: NfcScan;
  stage: WriteStage;
  club: string | null;
  error: string | null;
  onChoose: (clubId: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * The blank-tag flow: pick a club, confirm, write it onto the tag.
 *
 * Writing is not undoable from the rig the way a registry entry is -- the club
 * ends up in the tag's own memory and travels with it -- so the choice gets an
 * explicit confirmation step before anything is committed.
 */
export function ClubTagWriteFlow({ scan, stage, club, error, onChoose, onConfirm, onCancel }: ClubTagWriteFlowProps) {
  const { t } = useI18n();

  if (stage === 'select') {
    return (
      <PickerOverlay
        title={t('nfc.blankTagTitle')}
        subtitle={t('nfc.blankTagSubtitle', { uid: scan.uid_display })}
        selectedId=""
        sections={clubSections()}
        onSelect={onChoose}
        onClose={onCancel}
      />
    );
  }

  const clubName = getClubName(club ?? '');

  return (
    <div className="add-player-modal">
      <button type="button" className="add-player-modal__scrim" aria-label={t('nfc.cancelWrite')} onClick={onCancel} />
      <div
        className="add-player-modal__dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="club-tag-write-title"
        aria-describedby="club-tag-write-detail"
      >
        <span id="club-tag-write-title" className="add-player-modal__title">
          {stage === 'failed' ? t('nfc.writeFailedTitle') : t('nfc.writeConfirm', { club: clubName })}
        </span>
        <p id="club-tag-write-detail" className="clear-session-dialog__detail">
          {stage === 'writing' ? t('nfc.writeHold') : null}
          {stage === 'confirm' ? t('nfc.writeDetail', { club: clubName, uid: scan.uid_display }) : null}
          {stage === 'failed' ? `${error ?? ''} ${t('nfc.writeRetryDetail')}` : null}
        </p>
        {stage === 'writing' ? null : (
          <div className="add-player-modal__actions">
            <PanelAction variant="primary" autoFocus onClick={onConfirm}>
              {stage === 'failed' ? t('nfc.writeRetry') : t('nfc.writeAction')}
            </PanelAction>
            <PanelAction variant="secondary" onClick={onCancel}>
              {t('shutdown.cancel')}
            </PanelAction>
          </div>
        )}
      </div>
    </div>
  );
}
