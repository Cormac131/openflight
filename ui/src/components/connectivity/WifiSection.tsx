import { useRef } from 'react';
import { useI18n } from '../../i18n/useI18n';
import { useDragScroll } from '../../hooks/useDragScroll';
import { connectivityApi } from '../../services/connectivityApi';
import { runOperation as run } from '../../stores/useConnectivityStore';
import type { ConnectivityOperation, WifiNetwork, WifiStatus } from '../../types/connectivity';
import { errorKey, signalBars } from '../../utils/connectivity';
import { PanelAction } from '../panel/PanelAction';
import { LockIcon, WifiIcon } from './icons';

interface WifiSectionProps {
  wifi: WifiStatus;
  pendingOps: ConnectivityOperation[];
  /** Ask for a password (secured, unsaved) or an SSID (hidden network: `null`). */
  onRequestPassword: (network: WifiNetwork | null) => void;
}

export function WifiSection({ wifi, pendingOps, onRequestPassword }: WifiSectionProps) {
  const { t } = useI18n();
  const listRef = useRef<HTMLUListElement>(null);
  const dragScroll = useDragScroll(listRef);
  const isPending = (kind: string, target?: string | null) =>
    pendingOps.some((op) => op.kind === kind && (target === undefined || op.target === target));
  // One Wi-Fi change at a time, matching the server's busy group.
  const busy = pendingOps.some((op) => op.kind.startsWith('wifi_'));

  if (!wifi.available) {
    return <p className="conn-section__empty">{t(errorKey(wifi.reason))}</p>;
  }

  const toggle = (
    <PanelAction
      variant="secondary"
      disabled={busy}
      onClick={() => void run(() => connectivityApi.wifiSetEnabled(!wifi.enabled))}
    >
      {wifi.enabled ? t('connections.turnOff') : t('connections.turnOn')}
    </PanelAction>
  );

  if (!wifi.enabled) {
    return (
      <div className="conn-section">
        <div className="conn-row conn-row--header">
          <span className="conn-row__title">{t('connections.wifi.off')}</span>
          {toggle}
        </div>
      </div>
    );
  }

  const connectTo = (network: WifiNetwork) => {
    if (!network.supported || busy) return;
    if (network.needs_password && !network.saved) {
      onRequestPassword(network);
      return;
    }
    void run(() => connectivityApi.wifiConnect(network.ssid, null));
  };

  const scanning = wifi.scanning || isPending('wifi_scan');
  const connecting = isPending('wifi_connect');

  return (
    <div className="conn-section">
      <div className="conn-row conn-row--current">
        <div className="conn-row__main">
          <span className="conn-row__eyebrow">{t('connections.wifi.current')}</span>
          {wifi.state === 'connected' && wifi.ssid ? (
            <>
              <span className="conn-row__title">
                <WifiIcon bars={signalBars(wifi.signal)} /> {wifi.ssid}
              </span>
              <span className="conn-row__detail">
                {t('connections.wifi.signal', { percent: wifi.signal ?? 0 })}
                {wifi.ip4_address ? ` · ${t('connections.wifi.ip', { address: wifi.ip4_address })}` : ''}
              </span>
            </>
          ) : (
            <span className="conn-row__title">
              {wifi.state === 'connecting' || connecting
                ? t('connections.wifi.connecting')
                : t('connections.wifi.notConnected')}
            </span>
          )}
        </div>
        <div className="conn-row__actions">
          {wifi.state === 'connected' && wifi.ssid ? (
            <>
              <PanelAction
                variant="secondary"
                disabled={busy}
                onClick={() => void run(() => connectivityApi.wifiDisconnect())}
              >
                {t('connections.disconnect')}
              </PanelAction>
              <PanelAction
                variant="danger"
                disabled={busy}
                onClick={() => void run(() => connectivityApi.wifiForget(wifi.ssid ?? ''))}
              >
                {t('connections.forget')}
              </PanelAction>
            </>
          ) : null}
          {toggle}
        </div>
      </div>

      <div className="conn-row conn-row--header">
        <span className="conn-row__eyebrow">{t('connections.wifi.nearby')}</span>
        <div className="conn-row__actions">
          <PanelAction variant="secondary" onClick={() => onRequestPassword(null)} disabled={busy}>
            {t('connections.wifi.other')}
          </PanelAction>
          <PanelAction onClick={() => void run(() => connectivityApi.wifiScan())} disabled={busy} aria-busy={scanning}>
            {scanning ? t('connections.wifi.scanning') : t('connections.wifi.scan')}
          </PanelAction>
        </div>
      </div>

      <ul className="conn-list" ref={listRef} {...dragScroll} aria-label={t('connections.wifi.nearby')}>
        {wifi.networks.filter((network) => !network.active).length === 0 ? (
          <li className="conn-section__empty">
            {scanning ? t('connections.wifi.scanning') : t('connections.wifi.empty')}
          </li>
        ) : null}
        {wifi.networks
          .filter((network) => !network.active)
          .map((network) => {
            const bars = signalBars(network.signal);
            const security = !network.supported
              ? t('connections.wifi.unsupported')
              : network.needs_password
                ? t('connections.wifi.secured')
                : t('connections.wifi.open');
            const rowConnecting = isPending('wifi_connect', network.ssid);
            return (
              <li key={network.ssid} className="conn-item">
                <button
                  type="button"
                  className="conn-item__main"
                  disabled={!network.supported || busy}
                  onClick={() => connectTo(network)}
                  aria-label={t('connections.wifi.networkAria', { ssid: network.ssid, security, bars })}
                >
                  <WifiIcon bars={bars} />
                  <span className="conn-item__name">{network.ssid}</span>
                  {network.needs_password || !network.supported ? <LockIcon /> : null}
                  <span className="conn-item__meta">
                    {rowConnecting
                      ? t('connections.wifi.connecting')
                      : network.saved
                        ? t('connections.wifi.saved')
                        : security}
                  </span>
                </button>
                {network.saved ? (
                  <PanelAction
                    variant="danger"
                    disabled={busy}
                    onClick={() => void run(() => connectivityApi.wifiForget(network.ssid))}
                  >
                    {t('connections.forget')}
                  </PanelAction>
                ) : null}
              </li>
            );
          })}
      </ul>
    </div>
  );
}
