import { useEffect, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { useI18n } from '../../i18n/useI18n';
import { connectivityApi } from '../../services/connectivityApi';
import { useConnectivityStore } from '../../stores/useConnectivityStore';
import type {
  ConnectionsTab,
  ConnectivityOperation,
  ConnectivitySnapshot,
  DesktopStatus,
  WifiNetwork,
} from '../../types/connectivity';
import type { Notice } from '../../utils/connectivity';
import { INTERNET_KEYS } from '../../utils/connectivity';
import { PanelAction } from '../panel/PanelAction';
import { BluetoothSection } from './BluetoothSection';
import { WifiPasswordDialog } from './WifiPasswordDialog';
import { WifiSection } from './WifiSection';
import './connectivity.css';

interface ConnectionsPanelProps {
  initialTab?: ConnectionsTab;
  onClose: () => void;
  /** Overrides for SSR tests; omit to read `useConnectivityStore`. */
  snapshot?: ConnectivitySnapshot | null;
  desktop?: DesktopStatus | null;
  pendingOps?: ConnectivityOperation[];
  notice?: Notice | null;
}

const NOTICE_TIMEOUT_MS = 6000;

export function ConnectionsPanel({
  initialTab = 'wifi',
  onClose,
  snapshot: snapshotProp,
  desktop: desktopProp,
  pendingOps: pendingProp,
  notice: noticeProp,
}: ConnectionsPanelProps) {
  const { t } = useI18n();
  const store = useConnectivityStore(
    useShallow((state) => ({
      snapshot: state.snapshot,
      desktop: state.desktop,
      pending: state.pending,
      notice: state.notice,
    }))
  );
  const snapshot = snapshotProp !== undefined ? snapshotProp : store.snapshot;
  const desktop = desktopProp !== undefined ? desktopProp : store.desktop;
  const pendingOps = pendingProp ?? Object.values(store.pending);
  const notice = noticeProp !== undefined ? noticeProp : store.notice;
  const [tab, setTab] = useState<ConnectionsTab>(initialTab);
  const [passwordFor, setPasswordFor] = useState<WifiNetwork | null | undefined>(undefined);
  const [confirmDesktop, setConfirmDesktop] = useState(false);
  const [openingDesktop, setOpeningDesktop] = useState(false);

  // Fresh status on open; kick off one Wi-Fi scan so the list is current.
  useEffect(() => {
    const connectivity = useConnectivityStore.getState();
    void connectivity.load(true).then(() => {
      const wifi = useConnectivityStore.getState().snapshot?.wifi;
      if (initialTab === 'wifi' && wifi?.available && wifi.enabled) {
        void connectivityApi.wifiScan().then(
          (op) => useConnectivityStore.getState().applyOperation(op),
          () => undefined // Busy or unavailable: the existing list is still shown.
        );
      }
    });
  }, [initialTab]);

  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => useConnectivityStore.getState().dismissNotice(), NOTICE_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const showDesktop = async () => {
    setOpeningDesktop(true);
    try {
      await connectivityApi.showDesktop();
    } catch (error) {
      useConnectivityStore.getState().reportError(error);
      setOpeningDesktop(false);
      setConfirmDesktop(false);
    }
  };

  const tabs: { id: ConnectionsTab; label: string }[] = [
    { id: 'wifi', label: t('connections.tabWifi') },
    { id: 'bluetooth', label: t('connections.tabBluetooth') },
  ];
  if (desktop?.available) tabs.push({ id: 'advanced', label: t('connections.tabAdvanced') });
  const activeTab = tabs.some((entry) => entry.id === tab) ? tab : 'wifi';
  const network = snapshot?.network;

  return (
    <div className="conn-panel" role="dialog" aria-modal="true" aria-label={t('connections.title')}>
      <div className="picker-overlay__header conn-panel__header">
        <span className="picker-overlay__title">{t('connections.title')}</span>
        <div className="conn-panel__tabs" role="tablist" aria-label={t('connections.title')}>
          {tabs.map((entry) => (
            <PanelAction
              key={entry.id}
              role="tab"
              aria-selected={entry.id === activeTab}
              variant={entry.id === activeTab ? 'primary' : 'secondary'}
              onClick={() => setTab(entry.id)}
            >
              {entry.label}
            </PanelAction>
          ))}
        </div>
        <button type="button" className="picker-overlay__close" onClick={onClose} aria-label={t('connections.close')}>
          ✕
        </button>
      </div>

      {network ? (
        <div className="conn-summary" role="status">
          <span className={`conn-summary__item conn-summary__item--${network.local_network ? 'ok' : 'off'}`}>
            <span className="conn-summary__label">{t('connections.localNetwork')}</span>
            {network.local_network ? t('connections.local.connected') : t('connections.local.disconnected')}
          </span>
          <span
            className={`conn-summary__item conn-summary__item--${network.internet === 'online' ? 'ok' : network.internet === 'unknown' ? 'muted' : 'warn'}`}
          >
            <span className="conn-summary__label">{t('connections.internet')}</span>
            {t(INTERNET_KEYS[network.internet])}
          </span>
          {network.internet !== 'online' ? (
            <span className="conn-summary__hint">{t('connections.offlineOk')}</span>
          ) : null}
        </div>
      ) : null}

      {notice ? (
        <div className={`conn-notice conn-notice--${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>
          <span>{t(notice.key, notice.vars)}</span>
          <button
            type="button"
            className="conn-notice__dismiss"
            onClick={() => useConnectivityStore.getState().dismissNotice()}
            aria-label={t('connections.dismiss')}
          >
            ✕
          </button>
        </div>
      ) : null}

      <div className="conn-panel__body" role="tabpanel">
        {!snapshot ? (
          <p className="conn-section__empty">{t('connections.internet.unknown')}</p>
        ) : activeTab === 'wifi' ? (
          <WifiSection wifi={snapshot.wifi} pendingOps={pendingOps} onRequestPassword={setPasswordFor} />
        ) : activeTab === 'bluetooth' ? (
          <BluetoothSection bluetooth={snapshot.bluetooth} pendingOps={pendingOps} />
        ) : (
          <div className="conn-section conn-desktop">
            <span className="conn-row__eyebrow">{t('connections.desktop.title')}</span>
            <p className="conn-desktop__detail">{t('connections.desktop.detail')}</p>
            {confirmDesktop ? (
              <div className="conn-desktop__confirm" role="group" aria-label={t('connections.desktop.confirmTitle')}>
                <span className="conn-row__title">
                  {openingDesktop ? t('connections.desktop.opening') : t('connections.desktop.confirmTitle')}
                </span>
                <div className="conn-row__actions">
                  <PanelAction onClick={() => void showDesktop()} disabled={openingDesktop}>
                    {t('connections.desktop.button')}
                  </PanelAction>
                  <PanelAction variant="secondary" onClick={() => setConfirmDesktop(false)} disabled={openingDesktop}>
                    {t('shutdown.cancel')}
                  </PanelAction>
                </div>
              </div>
            ) : (
              <PanelAction variant="secondary" onClick={() => setConfirmDesktop(true)}>
                {t('connections.desktop.button')}
              </PanelAction>
            )}
          </div>
        )}
      </div>

      {passwordFor !== undefined ? (
        <WifiPasswordDialog network={passwordFor} onClose={() => setPasswordFor(undefined)} />
      ) : null}
    </div>
  );
}
