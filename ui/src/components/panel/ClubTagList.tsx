import { useRef } from 'react';
import { useI18n } from '../../i18n/useI18n';
import { useDragScroll } from '../../hooks/useDragScroll';
import { getClubName } from '../../data/clubs';
import type { ClubTag } from '../../types/nfc';

interface ClubTagListProps {
  tags: ClubTag[];
  onForget: (uid: string) => void;
}

/**
 * The learned club tags, with a way to drop one that was taught the wrong club.
 *
 * A full bag is fourteen rows, more than the menu sheet can show, so the list is
 * its own bounded drag-scroller rather than pushing Shut down off the sheet.
 */
export function ClubTagList({ tags, onForget }: ClubTagListProps) {
  const { t } = useI18n();
  const listRef = useRef<HTMLDivElement>(null);
  const dragScroll = useDragScroll(listRef);

  if (tags.length === 0) {
    return <span className="menu-sheet__status-value">{t('nfc.noTags')}</span>;
  }

  return (
    <div className="club-tag-list" ref={listRef} {...dragScroll}>
      {tags.map((tag) => (
        <div className="menu-sheet__status-row" key={tag.uid}>
          <span className="menu-sheet__status-label">{getClubName(tag.club)}</span>
          <span className="menu-sheet__status-value">{tag.uid_display}</span>
          <button
            type="button"
            className="menu-sheet__chip"
            aria-label={t('nfc.forgetTag', { club: getClubName(tag.club) })}
            onClick={() => onForget(tag.uid)}
          >
            {t('nfc.forget')}
          </button>
        </div>
      ))}
    </div>
  );
}
