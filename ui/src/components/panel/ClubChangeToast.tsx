import { useI18n } from '../../i18n/useI18n';
import { getClubName } from '../../data/clubs';

interface ClubChangeToastProps {
  clubId: string;
}

/**
 * Big glanceable confirmation that a tapped club tag changed the selection.
 *
 * Deliberately non-interactive: the player is standing at the mat holding a
 * club, and an overlay that could swallow a tap for two seconds is worse than
 * no overlay. It carries `pointer-events: none` and fades out on its own.
 */
export function ClubChangeToast({ clubId }: ClubChangeToastProps) {
  const { t } = useI18n();

  return (
    <div className="club-toast" role="status" aria-live="polite">
      <div className="club-toast__card">
        <span className="club-toast__label">{t('nfc.clubSelected')}</span>
        <span className="club-toast__club">{getClubName(clubId)}</span>
      </div>
    </div>
  );
}
