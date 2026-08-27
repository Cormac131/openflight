import { PanelAction } from './PanelAction';
import { useI18n } from '../../i18n/useI18n';

interface ProfileNameDialogProps {
  /** Add and rename differ only in copy and initial value, so one dialog serves both. */
  mode: 'add' | 'rename';
  name: string;
  onChange: (name: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ProfileNameDialog({ mode, name, onChange, onConfirm, onCancel }: ProfileNameDialogProps) {
  const { t } = useI18n();
  const canConfirm = Boolean(name.trim());
  const title = mode === 'add' ? t('menu.addProfile') : t('menu.renameProfile');

  return (
    <div className="profile-name-modal">
      <button
        type="button"
        className="profile-name-modal__scrim"
        aria-label={t('picker.close', { title })}
        onClick={onCancel}
      />
      <div className="profile-name-modal__dialog" role="dialog" aria-modal="true" aria-label={title}>
        <span id="profile-name-title" className="profile-name-modal__title">
          {title}
        </span>
        <input
          className="profile-name-modal__input"
          type="text"
          autoFocus
          maxLength={40}
          placeholder={t('profiles.namePlaceholder')}
          value={name}
          aria-labelledby="profile-name-title"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && canConfirm) onConfirm();
          }}
        />
        <div className="profile-name-modal__actions">
          <PanelAction disabled={!canConfirm} onClick={onConfirm}>
            {title}
          </PanelAction>
          <PanelAction variant="secondary" onClick={onCancel}>
            {t('shutdown.cancel')}
          </PanelAction>
        </div>
      </div>
    </div>
  );
}
