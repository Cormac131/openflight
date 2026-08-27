import { useMemo, useRef, type ReactNode } from 'react';
import type { Profile } from '../../types/profile';
import type { Shot } from '../../types/shot';
import { filterShotsByProfile } from '../../types/shot';
import { useDragScroll } from '../../hooks/useDragScroll';
import { useI18n } from '../../i18n/useI18n';
import { PanelHeader } from './PanelHeader';

interface ProfilesPanelProps {
  profiles: Profile[];
  activeProfileId: string;
  shots: Shot[];
  /** False until the server's first roster snapshot arrives. */
  loaded: boolean;
  onSelectProfile: (profileId: string) => void;
  onRenameProfile: (profile: Profile) => void;
  onRemoveProfile: (profileId: string) => void;
  /** Pinned header control, e.g. Add profile. */
  headerAction?: ReactNode;
}

export function ProfilesPanel({
  profiles,
  activeProfileId,
  shots,
  loaded,
  onSelectProfile,
  onRenameProfile,
  onRemoveProfile,
  headerAction,
}: ProfilesPanelProps) {
  const { t } = useI18n();
  const rosterRef = useRef<HTMLDivElement>(null);
  const dragScroll = useDragScroll(rosterRef);
  // The active profile can never be removed: deleting the profile whose shots
  // are on screen is a trap, and the server refuses it too.
  const canRemove = profiles.length > 1;
  const activeProfile = profiles.find((profile) => profile.id === activeProfileId) ?? null;
  const shotCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const shot of shots) {
      const id = shot.profile_id;
      if (!id) continue;
      counts[id] = (counts[id] ?? 0) + 1;
    }
    return counts;
  }, [shots]);

  return (
    <div className="panel">
      <PanelHeader title={t('nav.profiles')} subtitle={activeProfile?.name ?? ''} actions={headerAction} />
      <div
        className="panel__body profiles-panel__grid"
        role="region"
        aria-label={t('profiles.rosterAria')}
        aria-busy={!loaded}
        ref={rosterRef}
        onPointerDown={dragScroll.onPointerDown}
        onPointerMove={dragScroll.onPointerMove}
        onPointerUp={dragScroll.onPointerUp}
        onPointerCancel={dragScroll.onPointerCancel}
        onClickCapture={dragScroll.onClickCapture}
      >
        {!loaded ? (
          <div className="profiles-panel__skeleton" aria-hidden="true" />
        ) : (
          profiles.map((profile) => {
            const selected = profile.id === activeProfileId;
            const count = shotCounts[profile.id] ?? 0;
            const shotLabel = t(count === 1 ? 'profiles.shot' : 'profiles.shots', { count });

            return (
              <div className="profiles-panel__card-wrap" key={profile.id}>
                <button
                  type="button"
                  className={`profiles-panel__card${selected ? ' profiles-panel__card--selected' : ''}`}
                  aria-pressed={selected}
                  onClick={() => onSelectProfile(profile.id)}
                >
                  <span className="profiles-panel__name">{profile.name}</span>
                  <span className="profiles-panel__count">{shotLabel}</span>
                </button>
                <div className="profiles-panel__actions">
                  <button
                    type="button"
                    className="profiles-panel__rename"
                    aria-label={t('menu.renameProfileNamed', { name: profile.name })}
                    onClick={() => onRenameProfile(profile)}
                  >
                    ✎
                  </button>
                  {canRemove && !selected ? (
                    <button
                      type="button"
                      className="profiles-panel__remove"
                      aria-label={t('menu.removeProfile', { name: profile.name })}
                      onClick={() => onRemoveProfile(profile.id)}
                    >
                      ✕
                    </button>
                  ) : null}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
