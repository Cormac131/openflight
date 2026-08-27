import { useI18n } from '../../i18n/useI18n';
import { PickerOverlay } from './PickerOverlay';
import { clubSections } from './pickerSections';
import type { NfcScan } from '../../types/nfc';

interface ClubTagPromptProps {
  scan: NfcScan;
  onAssign: (clubId: string) => void;
  onDismiss: () => void;
}

/**
 * Asks which club an unrecognized NFC tag belongs to.
 *
 * Reuses the club picker so learning a tag looks exactly like choosing a club,
 * with no club preselected: the tag has no meaning yet, and a highlighted tile
 * would invite a mis-tap that then persists to disk.
 */
export function ClubTagPrompt({ scan, onAssign, onDismiss }: ClubTagPromptProps) {
  const { t } = useI18n();

  return (
    <PickerOverlay
      title={t('nfc.newTagTitle')}
      subtitle={t('nfc.newTagSubtitle', { uid: scan.uid_display })}
      selectedId=""
      sections={clubSections()}
      onSelect={onAssign}
      onClose={onDismiss}
    />
  );
}
