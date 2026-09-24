import { useShallow } from 'zustand/react/shallow';
import { useI18n } from '../../i18n/useI18n';
import { useConnectivityStore } from '../../stores/useConnectivityStore';
import type { ConnectionsTab, ConnectivitySnapshot } from '../../types/connectivity';
import { bluetoothIndicator, wifiIndicator } from '../../utils/connectivity';
import { BluetoothIcon, WifiIcon } from './icons';
import './connectivity.css';

interface ConnectivityIndicatorsProps {
  /** Defaults to opening the app-level Connections panel. */
  onOpen?: (tab: ConnectionsTab) => void;
  /**
   * Snapshot override for tests (SSR keeps the store's initial state).
   * Omit to read `useConnectivityStore`.
   */
  snapshot?: ConnectivitySnapshot | null;
}

/** `--connectivity off` or a non-Linux dev machine: nothing to control here. */
const PERMANENTLY_OFF = new Set(['disabled', 'unsupported_platform']);

function connectivityIsOff(snapshot: ConnectivitySnapshot): boolean {
  return [snapshot.wifi, snapshot.bluetooth].every(
    (radio) => !radio.available && PERMANENTLY_OFF.has(radio.reason ?? '')
  );
}

/**
 * Compact Wi-Fi + Bluetooth status buttons. Rendered only when the server
 * confirmed this browser is the kiosk itself (remote viewers get nothing).
 */
export function ConnectivityIndicators({ onOpen, snapshot: snapshotProp }: ConnectivityIndicatorsProps) {
  const { t } = useI18n();
  const { availability, storeSnapshot, openPanel } = useConnectivityStore(
    useShallow((state) => ({
      availability: state.availability,
      storeSnapshot: state.snapshot,
      openPanel: state.openPanel,
    }))
  );
  const open = onOpen ?? openPanel;
  const snapshot = snapshotProp !== undefined ? snapshotProp : availability === 'available' ? storeSnapshot : null;
  if (!snapshot || connectivityIsOff(snapshot)) return null;

  const wifi = wifiIndicator(snapshot.wifi);
  const bt = bluetoothIndicator(snapshot.bluetooth);
  const noInternet = wifi.state === 'connected' && ['limited', 'offline', 'portal'].includes(snapshot.network.internet);

  let wifiLabel =
    wifi.state === 'connected'
      ? t('connections.wifi.indicator.connected', { ssid: snapshot.wifi.ssid ?? '', bars: wifi.bars })
      : t(`connections.wifi.indicator.${wifi.state}`);
  if (noInternet) wifiLabel = t('connections.wifi.indicator.noInternet', { label: wifiLabel });
  const btLabel = !bt.on
    ? t('connections.bt.indicator.off')
    : bt.connected > 0
      ? t('connections.bt.indicator.connected', { count: bt.connected })
      : t('connections.bt.indicator.on');

  return (
    <div className="conn-indicators">
      <button
        type="button"
        className={`conn-indicator conn-indicator--wifi conn-indicator--${wifi.state}${noInternet ? ' conn-indicator--warn' : ''}`}
        onClick={() => open('wifi')}
        aria-label={wifiLabel}
        title={wifiLabel}
      >
        <WifiIcon bars={wifi.bars} off={wifi.state === 'off' || wifi.state === 'disconnected'} />
        {noInternet ? (
          <span className="conn-indicator__badge" aria-hidden="true">
            !
          </span>
        ) : null}
      </button>
      <button
        type="button"
        className={`conn-indicator conn-indicator--bt${bt.on ? '' : ' conn-indicator--off'}${bt.connected ? ' conn-indicator--active' : ''}`}
        onClick={() => open('bluetooth')}
        aria-label={btLabel}
        title={btLabel}
      >
        <BluetoothIcon off={!bt.on} />
        {bt.connected ? <span className="conn-indicator__count">{bt.connected}</span> : null}
      </button>
    </div>
  );
}
