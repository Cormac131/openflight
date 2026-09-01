import { useI18n } from '../../i18n/useI18n';
import { getClubName } from '../../data/clubs';
import { PickerOverlay } from './PickerOverlay';
import { clubSections } from './pickerSections';
import type { NfcScan } from '../../types/nfc';

interface ClubTagPromptProps {
  scan: NfcScan;
  error?: string | null;
  assigningClub?: string | null;
  onAssign: (clubId: string) => void;
  onDismiss: () => void;
  onForget?: () => void;
}

/**
 * Club picker for a scanned tag. An unknown tag has no club preselected.
 * A known tag highlights its club and offers Forget for that tag only.
 */
export function ClubTagPrompt({ scan, error, assigningClub, onAssign, onDismiss, onForget }: ClubTagPromptProps) {
  const { t } = useI18n();
  const title = scan.known ? t('nfc.tagTitle') : t('nfc.newTagTitle');
  const forgetClub = scan.club ? getClubName(scan.club) : scan.uid_display;
  const errorText = error ? `${t('nfc.assignFailed')}: ${error}` : null;

  return (
    <PickerOverlay
      title={title}
      subtitle={t('nfc.newTagSubtitle', { uid: scan.uid_display })}
      selectedId={assigningClub ?? scan.club ?? ''}
      sections={clubSections()}
      onSelect={onAssign}
      onClose={onDismiss}
      actionLabel={scan.known ? t('nfc.forget') : undefined}
      actionAriaLabel={scan.known ? t('nfc.forgetTag', { club: forgetClub }) : undefined}
      onAction={scan.known ? onForget : undefined}
      error={errorText}
    />
  );
}
