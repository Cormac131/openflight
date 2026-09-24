import { useRef } from 'react';
import { useI18n } from '../../i18n/useI18n';
import { useDragScroll } from '../../hooks/useDragScroll';
import { connectivityApi } from '../../services/connectivityApi';
import { runOperation as run } from '../../stores/useConnectivityStore';
import type { BluetoothDevice, BluetoothStatus, ConnectivityOperation } from '../../types/connectivity';
import { errorKey, splitDevices } from '../../utils/connectivity';
import { PanelAction } from '../panel/PanelAction';
import { BluetoothIcon } from './icons';

interface BluetoothSectionProps {
  bluetooth: BluetoothStatus;
  pendingOps: ConnectivityOperation[];
}

export function BluetoothSection({ bluetooth, pendingOps }: BluetoothSectionProps) {
  const { t } = useI18n();
  const listRef = useRef<HTMLDivElement>(null);
  const dragScroll = useDragScroll(listRef);
  // One Bluetooth change at a time, matching the server's busy group.
  const busy = pendingOps.some((op) => op.kind.startsWith('bluetooth_') && op.kind !== 'bluetooth_discovery');
  const pendingFor = (address: string) => pendingOps.find((op) => op.target === address);

  if (!bluetooth.available) {
    return <p className="conn-section__empty">{t(errorKey(bluetooth.reason))}</p>;
  }

  const toggle = (
    <PanelAction
      variant="secondary"
      disabled={busy}
      onClick={() => void run(() => connectivityApi.bluetoothPower(!bluetooth.powered))}
    >
      {bluetooth.powered ? t('connections.turnOff') : t('connections.turnOn')}
    </PanelAction>
  );

  if (!bluetooth.powered) {
    return (
      <div className="conn-section">
        <div className="conn-row conn-row--header">
          <span className="conn-row__title">{t('connections.bt.off')}</span>
          {toggle}
        </div>
      </div>
    );
  }

  const { paired, nearby } = splitDevices(bluetooth.devices);

  const deviceRow = (device: BluetoothDevice) => {
    const op = pendingFor(device.address);
    const action = (
      label: string,
      kind: 'pair' | 'connect' | 'disconnect' | 'forget',
      variant: 'primary' | 'secondary' | 'danger'
    ) => (
      <PanelAction
        key={kind}
        variant={variant}
        disabled={busy}
        onClick={() => void run(() => connectivityApi.bluetoothDevice(device.address, kind))}
      >
        {label}
      </PanelAction>
    );
    return (
      <li key={device.address} className="conn-item">
        <div className="conn-item__main conn-item__main--static">
          <BluetoothIcon />
          <span className="conn-item__name">{device.name}</span>
          <span className="conn-item__meta">
            {op ? t('connections.working') : device.connected ? t('connections.bt.connected') : ''}
          </span>
        </div>
        <div className="conn-item__actions">
          {device.paired
            ? [
                device.connected
                  ? action(t('connections.disconnect'), 'disconnect', 'secondary')
                  : action(t('connections.connect'), 'connect', 'primary'),
                action(t('connections.forget'), 'forget', 'danger'),
              ]
            : action(t('connections.bt.pair'), 'pair', 'primary')}
        </div>
      </li>
    );
  };

  return (
    <div className="conn-section">
      <div className="conn-row conn-row--header">
        <span className="conn-row__title">{bluetooth.adapter_name ?? t('connections.bt.radio')}</span>
        <div className="conn-row__actions">
          <PanelAction
            aria-busy={bluetooth.discovering}
            onClick={() => void run(() => connectivityApi.bluetoothDiscovery(!bluetooth.discovering))}
          >
            {bluetooth.discovering ? t('connections.bt.stopScan') : t('connections.bt.scan')}
          </PanelAction>
          {toggle}
        </div>
      </div>
      <div className="conn-list" ref={listRef} {...dragScroll}>
        <span className="conn-row__eyebrow">{t('connections.bt.paired')}</span>
        <ul className="conn-sublist" aria-label={t('connections.bt.paired')}>
          {paired.length ? (
            paired.map(deviceRow)
          ) : (
            <li className="conn-section__empty">{t('connections.bt.noPaired')}</li>
          )}
        </ul>
        <span className="conn-row__eyebrow">
          {t('connections.bt.nearby')}
          {bluetooth.discovering ? ` · ${t('connections.bt.scanning')}` : ''}
        </span>
        <ul className="conn-sublist" aria-label={t('connections.bt.nearby')}>
          {nearby.length ? (
            nearby.map(deviceRow)
          ) : (
            <li className="conn-section__empty">{t('connections.bt.noNearby')}</li>
          )}
        </ul>
      </div>
    </div>
  );
}
