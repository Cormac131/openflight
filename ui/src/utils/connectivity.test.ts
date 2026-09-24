import { describe, expect, it } from 'vitest';
import type { BluetoothStatus, ConnectivityOperation, WifiStatus } from '../types/connectivity';
import {
  bluetoothIndicator,
  errorKey,
  isValidSsid,
  isValidWifiPassword,
  noticeForOperation,
  signalBars,
  splitDevices,
  wifiIndicator,
} from './connectivity';
import { en } from '../i18n/en';

const wifi = (overrides: Partial<WifiStatus> = {}): WifiStatus => ({
  available: true,
  reason: null,
  enabled: true,
  interface: 'wlan0',
  state: 'connected',
  ssid: 'Home',
  signal: 80,
  ip4_address: null,
  scanning: false,
  networks: [],
  saved_networks: [],
  ...overrides,
});

const op = (overrides: Partial<ConnectivityOperation>): ConnectivityOperation => ({
  id: 'op-1',
  kind: 'wifi_connect',
  target: 'Home',
  state: 'succeeded',
  error: null,
  ...overrides,
});

describe('signalBars', () => {
  it.each([
    [null, 0],
    [0, 0],
    [1, 1],
    [24, 1],
    [25, 2],
    [50, 3],
    [74, 3],
    [75, 4],
    [100, 4],
  ])('%s%% -> %s bars', (signal, bars) => {
    expect(signalBars(signal)).toBe(bars);
  });
});

describe('noticeForOperation', () => {
  it('announces successful connections with the target', () => {
    expect(noticeForOperation(op({}))).toEqual({
      id: 'op-1',
      tone: 'success',
      key: 'connections.done.wifiConnect',
      vars: { target: 'Home' },
    });
  });

  it('keeps scans, toggles and discovery quiet on success', () => {
    for (const kind of ['wifi_scan', 'wifi_enable', 'bluetooth_power', 'bluetooth_discovery'] as const) {
      expect(noticeForOperation(op({ kind }))).toBeNull();
    }
  });

  it('reports scan failures', () => {
    const notice = noticeForOperation(op({ kind: 'wifi_scan', state: 'failed', error: { code: 'busy', message: '' } }));
    expect(notice?.key).toBe('connections.error.busy');
  });

  it('distinguishes wrong Wi-Fi passwords from failed Bluetooth pairing', () => {
    const failed = { state: 'failed' as const, error: { code: 'auth_failed' as const, message: '' } };
    expect(noticeForOperation(op({ ...failed }))?.key).toBe('connections.error.wifiAuth');
    expect(noticeForOperation(op({ ...failed, kind: 'bluetooth_pair' }), 'Speaker')).toMatchObject({
      key: 'connections.error.btAuth',
      vars: { target: 'Speaker' },
    });
  });

  it('ignores pending operations', () => {
    expect(noticeForOperation(op({ state: 'pending' }))).toBeNull();
  });
});

describe('errorKey', () => {
  it('maps every code to an existing message and falls back to failed', () => {
    for (const code of ['auth_failed', 'permission_denied', 'forbidden', 'network', 'no_adapter']) {
      expect(errorKey(code) in en).toBe(true);
    }
    expect(errorKey('something_new')).toBe('connections.error.failed');
    expect(errorKey(null)).toBe('connections.error.failed');
  });
});

describe('indicators', () => {
  it('wifi states', () => {
    expect(wifiIndicator(undefined)).toEqual({ state: 'off', bars: 0 });
    expect(wifiIndicator(wifi({ available: false }))).toEqual({ state: 'off', bars: 0 });
    expect(wifiIndicator(wifi({ enabled: false }))).toEqual({ state: 'off', bars: 0 });
    expect(wifiIndicator(wifi({ state: 'disconnected' }))).toEqual({ state: 'disconnected', bars: 0 });
    expect(wifiIndicator(wifi({ state: 'connecting' }))).toEqual({ state: 'connecting', bars: 0 });
    expect(wifiIndicator(wifi({ signal: 40 }))).toEqual({ state: 'connected', bars: 2 });
  });

  it('bluetooth counts connected devices only when powered', () => {
    const bt: BluetoothStatus = {
      available: true,
      reason: null,
      powered: true,
      discovering: false,
      adapter_name: 'pi',
      devices: [
        { address: 'A', name: 'A', icon: null, paired: true, trusted: true, connected: true, rssi: null },
        { address: 'B', name: 'B', icon: null, paired: false, trusted: false, connected: false, rssi: -60 },
      ],
    };
    expect(bluetoothIndicator(bt)).toEqual({ on: true, connected: 1 });
    expect(bluetoothIndicator({ ...bt, powered: false })).toEqual({ on: false, connected: 0 });
    expect(splitDevices(bt.devices).paired.map((d) => d.address)).toEqual(['A']);
    expect(splitDevices(bt.devices).nearby.map((d) => d.address)).toEqual(['B']);
  });
});

describe('credential validation mirrors the server', () => {
  it.each([
    ['12345678', true],
    ['x'.repeat(63), true],
    ['a'.repeat(64), true],
    ['1234567', false],
    ['x'.repeat(64), false],
    ['pässwörd12', false],
    ['', false],
  ])('password %j -> %s', (password, valid) => {
    expect(isValidWifiPassword(password)).toBe(valid);
  });

  it('limits SSIDs to 32 bytes', () => {
    expect(isValidSsid('')).toBe(false);
    expect(isValidSsid('x'.repeat(32))).toBe(true);
    expect(isValidSsid('é'.repeat(17))).toBe(false);
  });
});
