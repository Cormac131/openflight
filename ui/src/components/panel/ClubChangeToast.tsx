import { useI18n } from '../../i18n/useI18n';
import { getClubName } from '../../data/clubs';

interface ClubChangeToastProps {
  clubId: string;
  onChangeTag: () => void;
}

/**
 * Glanceable confirmation that a tapped club tag changed the selection.
 *
 * The dimmed backdrop does not intercept taps. The card does, so Change tag
 * can open the editor without blocking the rest of the kiosk while it fades.
 */
export function ClubChangeToast({ clubId, onChangeTag }: ClubChangeToastProps) {
  const { t } = useI18n();
  const clubName = getClubName(clubId);

  return (
    <div className="club-toast" role="status" aria-live="polite">
      <div className="club-toast__card">
        <span className="club-toast__label">{t('nfc.clubSelected')}</span>
        <span className="club-toast__club">{clubName}</span>
        <button
          type="button"
          className="club-toast__change"
          aria-label={t('nfc.changeTagFor', { club: clubName })}
          onClick={onChangeTag}
        >
          {t('nfc.changeTag')}
        </button>
      </div>
    </div>
  );
}
