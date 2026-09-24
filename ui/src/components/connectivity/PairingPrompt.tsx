import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n';
import { connectivityApi } from '../../services/connectivityApi';
import { useConnectivityStore } from '../../stores/useConnectivityStore';
import type { PairingRequest } from '../../types/connectivity';
import { PanelAction } from '../panel/PanelAction';
import { OnScreenKeyboard } from '../ui/OnScreenKeyboard';

const DIGITS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'] as const;

/**
 * Bluetooth pairing prompt from the BlueZ agent. Shown above everything,
 * whether or not the Connections panel is open, because the device is
 * waiting (about 25 s) for an answer.
 */
export function PairingPrompt({ request }: { request: PairingRequest }) {
  const { t } = useI18n();
  const [value, setValue] = useState('');
  const [sending, setSending] = useState(false);
  const vars = { name: request.name };
  const displayOnly = request.kind === 'display_passkey' || request.kind === 'display_pin';
  const needsValue = request.kind === 'enter_passkey' || request.kind === 'enter_pin';
  const valueValid =
    request.kind === 'enter_passkey'
      ? /^\d{1,6}$/.test(value)
      : request.kind === 'enter_pin'
        ? /^[0-9A-Za-z]{1,16}$/.test(value)
        : true;

  const message = {
    confirm: t('connections.pair.confirm', vars),
    enter_passkey: t('connections.pair.enterPasskey', vars),
    enter_pin: t('connections.pair.enterPin', vars),
    display_passkey: t('connections.pair.displayPasskey', vars),
    display_pin: t('connections.pair.displayPin', vars),
    authorize: t('connections.pair.authorize', vars),
  }[request.kind];

  const respond = async (accept: boolean) => {
    if (sending) return;
    setSending(true);
    const store = useConnectivityStore.getState();
    try {
      await connectivityApi.respondPairing(request.id, accept, accept && needsValue ? value : null);
      store.removePairing(request.id);
    } catch (error) {
      store.reportError(error);
      // A stale prompt (already answered or expired) must not stay on screen.
      store.removePairing(request.id);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="conn-pairing" role="alertdialog" aria-modal="true" aria-labelledby="pairing-title">
      <div className="conn-pairing__dialog">
        <h2 id="pairing-title" className="conn-pairing__title">
          {t('connections.pair.title', vars)}
        </h2>
        <p className="conn-pairing__message">{message}</p>
        {request.passkey ? (
          <div className="conn-pairing__code" aria-label={t('connections.pair.code')}>
            {request.passkey}
          </div>
        ) : null}
        {needsValue ? (
          <>
            <input
              className="profile-name-modal__input conn-pairing__input"
              inputMode="none"
              autoComplete="off"
              value={value}
              readOnly
              aria-label={t('connections.pair.code')}
            />
            {request.kind === 'enter_passkey' ? (
              <div className="osk conn-pairing__keypad" role="group" aria-label={t('keyboard.aria')}>
                <div className="osk__row">
                  {DIGITS.map((digit) => (
                    <button
                      key={digit}
                      type="button"
                      className="osk__key"
                      onClick={() => setValue((current) => (current.length < 6 ? current + digit : current))}
                    >
                      {digit}
                    </button>
                  ))}
                  <button
                    type="button"
                    className="osk__key osk__key--mod"
                    aria-label={t('keyboard.backspace')}
                    onClick={() => setValue((current) => current.slice(0, -1))}
                  >
                    ⌫
                  </button>
                </div>
              </div>
            ) : (
              <OnScreenKeyboard value={value} onChange={setValue} maxLength={16} layout="text" />
            )}
          </>
        ) : null}
        <div className="conn-pairing__buttons">
          {displayOnly ? (
            <PanelAction onClick={() => void respond(true)} disabled={sending}>
              {t('connections.pair.done')}
            </PanelAction>
          ) : (
            <PanelAction onClick={() => void respond(true)} disabled={sending || (needsValue && !valueValid)}>
              {t('connections.pair.accept')}
            </PanelAction>
          )}
          <PanelAction variant="secondary" onClick={() => void respond(false)} disabled={sending}>
            {t('connections.pair.reject')}
          </PanelAction>
        </div>
      </div>
    </div>
  );
}
