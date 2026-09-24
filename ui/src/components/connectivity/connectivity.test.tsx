import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ConnectivitySnapshot, PairingRequest, WifiNetwork } from '../../types/connectivity';
import { ConnectionsPanel } from './ConnectionsPanel';
import { ConnectivityIndicators } from './ConnectivityIndicators';
import { PairingPrompt } from './PairingPrompt';
import { WifiPasswordDialog } from './WifiPasswordDialog';
import { PickerOverlay } from '../panel/PickerOverlay';
import { MenuSheet } from '../panel/MenuSheet';

/** React SSR splits interpolated text with comment markers; drop them. */
function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

const network = (overrides: Partial<WifiNetwork>): WifiNetwork => ({
  ssid: 'Cafe',
  signal: 60,
  security: 'open',
  frequency_mhz: 2437,
  saved: false,
  active: false,
  supported: true,
  needs_password: false,
  ...overrides,
});

const snapshot = (overrides: Partial<ConnectivitySnapshot> = {}): ConnectivitySnapshot => ({
  wifi: {
    available: true,
    reason: null,
    enabled: true,
    interface: 'wlan0',
    state: 'connected',
    ssid: 'Range',
    signal: 82,
    ip4_address: '192.168.4.23',
    scanning: false,
    networks: [
      network({ ssid: 'Range', signal: 82, security: 'wpa-psk', needs_password: true, saved: true, active: true }),
      network({ ssid: 'Pro Shop', signal: 38, security: 'sae', needs_password: true }),
      network({ ssid: 'Members', signal: 20, security: 'enterprise', supported: false }),
      network({ ssid: 'Home', signal: 50, security: 'wpa-psk', needs_password: true, saved: true }),
    ],
    saved_networks: ['Home', 'Range'],
  },
  bluetooth: {
    available: true,
    reason: null,
    powered: true,
    discovering: false,
    adapter_name: 'openflight',
    devices: [
      { address: 'AA:01', name: 'Headphones', icon: null, paired: true, trusted: true, connected: true, rssi: -40 },
      { address: 'AA:02', name: 'Printer', icon: null, paired: false, trusted: false, connected: false, rssi: -70 },
    ],
  },
  network: { local_network: true, internet: 'online', source: 'networkmanager' },
  operations: [],
  pairing: [],
  ...overrides,
});

const renderPanel = (props: Partial<Parameters<typeof ConnectionsPanel>[0]> = {}) =>
  text(
    renderToString(
      <ConnectionsPanel
        onClose={() => {}}
        snapshot={snapshot()}
        desktop={null}
        pendingOps={[]}
        notice={null}
        {...props}
      />
    )
  );

describe('ConnectivityIndicators', () => {
  it('renders nothing until the server confirms this is the kiosk', () => {
    expect(renderToString(<ConnectivityIndicators onOpen={() => {}} snapshot={null} />)).toBe('');
  });

  it('describes the current Wi-Fi network and Bluetooth devices', () => {
    const html = text(renderToString(<ConnectivityIndicators onOpen={() => {}} snapshot={snapshot()} />));
    expect(html).toContain('aria-label="Wi-Fi: Range, 4 of 4 bars"');
    expect(html).toContain('aria-label="Bluetooth: 1 connected"');
    expect(html).not.toContain('conn-indicator--warn');
  });

  it('flags a Wi-Fi link without internet', () => {
    const html = text(
      renderToString(
        <ConnectivityIndicators
          onOpen={() => {}}
          snapshot={snapshot({ network: { local_network: true, internet: 'limited', source: 'probe' } })}
        />
      )
    );
    expect(html).toContain('Wi-Fi: Range, 4 of 4 bars, no internet');
    expect(html).toContain('conn-indicator--warn');
  });

  it('hides entirely when connectivity is disabled or unsupported here', () => {
    const base = snapshot();
    for (const reason of ['disabled', 'unsupported_platform'] as const) {
      const off = {
        ...base,
        wifi: { ...base.wifi, available: false, reason },
        bluetooth: { ...base.bluetooth, available: false, reason },
      };
      expect(renderToString(<ConnectivityIndicators onOpen={() => {}} snapshot={off} />)).toBe('');
    }
  });

  it('shows radios that are off', () => {
    const base = snapshot();
    const html = text(
      renderToString(
        <ConnectivityIndicators
          onOpen={() => {}}
          snapshot={{
            ...base,
            wifi: { ...base.wifi, enabled: false },
            bluetooth: { ...base.bluetooth, powered: false },
          }}
        />
      )
    );
    expect(html).toContain('aria-label="Wi-Fi off"');
    expect(html).toContain('aria-label="Bluetooth off"');
  });
});

describe('ConnectionsPanel', () => {
  it('separates local network from internet and reassures about offline use', () => {
    const html = renderPanel({
      snapshot: snapshot({ network: { local_network: true, internet: 'portal', source: 'networkmanager' } }),
    });
    expect(html).toContain('Local network</span>Connected');
    expect(html).toContain('Internet</span>Sign-in required');
    expect(html).toContain('OpenFlight works without internet.');
  });

  it('omits the offline hint when online', () => {
    expect(renderPanel()).not.toContain('OpenFlight works without internet.');
  });

  it('shows the current network with disconnect and forget, and nearby networks', () => {
    const html = renderPanel();
    expect(html).toContain('Current network');
    expect(html).toContain('Signal 82% · IP 192.168.4.23');
    expect(html).toContain('>Disconnect<');
    expect(html).toContain('aria-label="Pro Shop, Secured, 2 of 4 bars"');
    // Enterprise networks are listed but cannot be joined from the kiosk.
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*aria-label="Members, Not supported, 1 of 4 bars"/);
    // The active network is not repeated in the nearby list.
    expect(html).not.toContain('aria-label="Range,');
    // Saved networks can be forgotten from the list.
    expect(html.match(/>Forget</g)?.length).toBe(2);
  });

  it('locks Wi-Fi actions while a Wi-Fi change is running', () => {
    const html = renderPanel({
      pendingOps: [{ id: 'op-1', kind: 'wifi_connect', target: 'Pro Shop', state: 'pending', error: null }],
    });
    expect(html).toMatch(/aria-label="Pro Shop[^"]*"[^>]*>[\s\S]*?Connecting…/);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Disconnect</);
  });

  it('explains why Wi-Fi is unavailable', () => {
    const base = snapshot();
    const html = renderPanel({
      snapshot: { ...base, wifi: { ...base.wifi, available: false, reason: 'permission_denied' } },
    });
    expect(html).toContain('OpenFlight is not allowed to change this.');
  });

  it('offers turning Wi-Fi back on', () => {
    const base = snapshot();
    const html = renderPanel({ snapshot: { ...base, wifi: { ...base.wifi, enabled: false } } });
    expect(html).toContain('Wi-Fi is off.');
    expect(html).toContain('>Turn on<');
  });

  it('lists paired and nearby Bluetooth devices with their actions', () => {
    const html = renderPanel({ initialTab: 'bluetooth' });
    expect(html).toContain('Paired devices');
    expect(html).toMatch(/Headphones[\s\S]*>Disconnect<[\s\S]*>Forget</);
    expect(html).toMatch(/Nearby devices[\s\S]*Printer[\s\S]*>Pair</);
    expect(html).toContain('>Find devices<');
  });

  it('shows the Advanced tab only when a desktop session is available', () => {
    expect(renderPanel()).not.toContain('>Advanced<');
    const html = renderPanel({
      initialTab: 'advanced',
      desktop: { available: true, reason: null, mode: 'kiosk' },
    });
    expect(html).toContain('>Advanced<');
    expect(html).toContain('Return to OpenFlight');
    expect(html).toContain('>Show desktop<');
  });

  it('falls back to Wi-Fi if Advanced is requested without a desktop', () => {
    expect(renderPanel({ initialTab: 'advanced' })).toContain('Current network');
  });

  it('renders error notices as alerts', () => {
    const html = renderPanel({
      notice: { id: 'n', tone: 'error', key: 'connections.error.wifiAuth', vars: { target: 'Pro Shop' } },
    });
    expect(html).toContain('role="alert"');
    expect(html).toContain('Wrong password for Pro Shop.');
  });
});

describe('WifiPasswordDialog', () => {
  it('masks the password, caps its length and keeps the native keyboard down', () => {
    const html = text(
      renderToString(
        <WifiPasswordDialog
          network={network({ ssid: 'Pro Shop', security: 'sae', needs_password: true })}
          onClose={() => {}}
        />
      )
    );
    expect(html).toContain('Password for Pro Shop');
    expect(html).toContain('type="password"');
    expect(html).toContain('maxLength="64"');
    expect(html).toContain('inputMode="none"');
    expect(html).toContain('autoComplete="off"');
    expect(html).toContain('aria-label="Keyboard"');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Connect</);
  });

  it('asks for the network name first for hidden networks', () => {
    const html = text(renderToString(<WifiPasswordDialog network={null} onClose={() => {}} />));
    expect(html).toContain('Hidden network');
    expect(html).toContain('placeholder="Network name"');
    expect(html).toContain('maxLength="32"');
    expect(html).toMatch(/>Next</);
  });
});

describe('PairingPrompt', () => {
  const request = (overrides: Partial<PairingRequest>): PairingRequest => ({
    id: 'pair-1',
    address: 'AA:02',
    name: 'Printer',
    kind: 'confirm',
    passkey: '123456',
    expires_in_s: 25,
    ...overrides,
  });

  it('shows the passkey to compare', () => {
    const html = text(renderToString(<PairingPrompt request={request({})} />));
    expect(html).toContain('role="alertdialog"');
    expect(html).toContain('Pair with Printer');
    expect(html).toContain('>123456<');
    expect(html).toContain('>Pair<');
    expect(html).toContain('>Cancel<');
  });

  it('offers a digit keypad for passkey entry and waits for input', () => {
    const html = text(renderToString(<PairingPrompt request={request({ kind: 'enter_passkey', passkey: null })} />));
    expect(html).toContain('Enter the 6-digit code shown on Printer.');
    expect(html).toContain('>0<');
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Pair</);
  });

  it('uses Done for display-only codes', () => {
    const html = text(renderToString(<PairingPrompt request={request({ kind: 'display_passkey' })} />));
    expect(html).toContain('Type this code on Printer');
    expect(html).toContain('>Done<');
  });
});

describe('entry points', () => {
  it('puts the indicators in the first-launch picker header', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="driver"
        sections={[{ name: 'Woods', options: [{ id: 'driver', label: 'Driver' }] }]}
        onSelect={() => {}}
        onClose={() => {}}
        headerExtra={<span className="probe">indicators</span>}
      />
    );
    expect(html).toContain('picker-overlay__header-extra');
    expect(html).toContain('indicators');
  });

  it('adds a menu entry only when connections are available', () => {
    expect(renderToString(<MenuSheet onClose={() => {}} onShutdown={() => {}} />)).not.toContain(
      'Wi-Fi &amp; Bluetooth'
    );
    expect(
      renderToString(<MenuSheet onClose={() => {}} onShutdown={() => {}} onOpenConnections={() => {}} />)
    ).toContain('Wi-Fi &amp; Bluetooth');
  });
});
