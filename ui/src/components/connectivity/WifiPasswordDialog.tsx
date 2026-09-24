import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n';
import { connectivityApi } from '../../services/connectivityApi';
import { runOperation } from '../../stores/useConnectivityStore';
import type { WifiNetwork } from '../../types/connectivity';
import { isValidSsid, isValidWifiPassword, MAX_SSID_BYTES } from '../../utils/connectivity';
import { PanelAction } from '../panel/PanelAction';
import { OnScreenKeyboard } from '../ui/OnScreenKeyboard';

const PASSWORD_MAX = 64;

interface WifiPasswordDialogProps {
  /** `null` joins a hidden network: ask for its name first. */
  network: WifiNetwork | null;
  onClose: () => void;
}

/**
 * Full-screen on-screen keyboard for Wi-Fi credentials. The password lives
 * only in this component's state and is dropped as soon as it is sent.
 */
export function WifiPasswordDialog({ network, onClose }: WifiPasswordDialogProps) {
  const { t } = useI18n();
  const hidden = network === null;
  const [step, setStep] = useState<'ssid' | 'password'>(hidden ? 'ssid' : 'password');
  const [ssid, setSsid] = useState(network?.ssid ?? '');
  const [password, setPassword] = useState('');
  const [reveal, setReveal] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const editingSsid = step === 'ssid';
  const value = editingSsid ? ssid : password;
  const setValue = editingSsid ? setSsid : setPassword;
  // Hidden networks may be open, so an empty password is allowed there.
  const canSubmit = editingSsid ? isValidSsid(ssid) : (hidden && password === '') || isValidWifiPassword(password);
  const title = editingSsid ? t('connections.wifi.hiddenTitle') : t('connections.wifi.passwordFor', { ssid });

  const submit = async () => {
    if (!canSubmit || submitting) return;
    if (editingSsid) {
      setStep('password');
      return;
    }
    setSubmitting(true);
    const secret = password;
    setPassword('');
    const started = await runOperation(() => connectivityApi.wifiConnect(ssid, secret || null, hidden));
    setSubmitting(false);
    if (started) onClose();
  };

  return (
    <div className="profile-name-modal conn-dialog" role="dialog" aria-modal="true" aria-label={title}>
      <div className="profile-name-modal__header">
        <span id="wifi-password-title" className="profile-name-modal__title">
          {title}
        </span>
        <button
          type="button"
          className="profile-name-modal__close"
          aria-label={t('profiles.closeDialog')}
          onClick={onClose}
        >
          ✕
        </button>
      </div>
      <div className="conn-dialog__field">
        <input
          className="profile-name-modal__input"
          type={editingSsid || reveal ? 'text' : 'password'}
          inputMode="none"
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          autoFocus
          maxLength={editingSsid ? MAX_SSID_BYTES : PASSWORD_MAX}
          placeholder={editingSsid ? t('connections.wifi.networkName') : t('connections.wifi.password')}
          value={value}
          aria-labelledby="wifi-password-title"
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void submit();
          }}
        />
        {editingSsid ? null : (
          <PanelAction variant="secondary" onClick={() => setReveal((on) => !on)} aria-pressed={reveal}>
            {reveal ? t('connections.wifi.hide') : t('connections.wifi.show')}
          </PanelAction>
        )}
      </div>
      {editingSsid ? null : <span className="conn-dialog__hint">{t('connections.wifi.passwordHint')}</span>}
      <OnScreenKeyboard
        key={step}
        value={value}
        onChange={setValue}
        maxLength={editingSsid ? MAX_SSID_BYTES : PASSWORD_MAX}
        layout="text"
      />
      <div className="profile-name-modal__actions">
        <PanelAction disabled={!canSubmit || submitting} onClick={() => void submit()}>
          {editingSsid ? t('connections.wifi.next') : t('connections.connect')}
        </PanelAction>
        <PanelAction variant="secondary" onClick={onClose}>
          {t('shutdown.cancel')}
        </PanelAction>
      </div>
    </div>
  );
}
