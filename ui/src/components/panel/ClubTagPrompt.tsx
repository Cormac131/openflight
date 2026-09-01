import { useI18n } from '../../i18n/useI18n';
import { getClubName } from '../../data/clubs';
import { PickerOverlay } from './PickerOverlay';
import { clubSections } from './pickerSections';
import type { NfcScan } from '../../types/nfc';

interface ClubTagPromptProps {
  scan: NfcScan;
  onAssign: (clubId: string) => void;
  onDismiss: () => void;
  onForget?: () => void;
}

/**
 * Club picker for a scanned tag. An unknown tag has no club preselected.
 * A known tag highlights its club and offers Forget for that tag only.
 */
export function ClubTagPrompt({ scan, onAssign, onDismiss, onForget }: ClubTagPromptProps) {
  const { t } = useI18n();
  const title = scan.known ? t('nfc.tagTitle') : t('nfc.newTagTitle');
  const forgetClub = scan.club ? getClubName(scan.club) : scan.uid_display;

  return (
    <PickerOverlay
      title={title}
      subtitle={t('nfc.newTagSubtitle', { uid: scan.uid_display })}
      selectedId={scan.club ?? ''}
      sections={clubSections()}
      onSelect={onAssign}
      onClose={onDismiss}
      actionLabel={scan.known ? t('nfc.forget') : undefined}
      actionAriaLabel={scan.known ? t('nfc.forgetTag', { club: forgetClub }) : undefined}
      onAction={scan.known ? onForget : undefined}
    />
  );
}
