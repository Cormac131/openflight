import type { MessageKey } from '../i18n';
import type {
  BluetoothDevice,
  BluetoothStatus,
  ConnectivityErrorCode,
  ConnectivityOperation,
  InternetState,
  WifiStatus,
} from '../types/connectivity';

/** 0-100 signal to 0-4 bars. */
export function signalBars(signal: number | null | undefined): 0 | 1 | 2 | 3 | 4 {
  if (signal == null || signal <= 0) return 0;
  if (signal >= 75) return 4;
  if (signal >= 50) return 3;
  if (signal >= 25) return 2;
  return 1;
}

export const ERROR_KEYS: Record<ConnectivityErrorCode, MessageKey> = {
  unsupported_platform: 'connections.error.unsupported_platform',
  disabled: 'connections.error.disabled',
  service_unavailable: 'connections.error.service_unavailable',
  no_adapter: 'connections.error.no_adapter',
  permission_denied: 'connections.error.permission_denied',
  invalid_request: 'connections.error.invalid_request',
  unsupported_security: 'connections.error.unsupported_security',
  auth_failed: 'connections.error.auth_failed',
  not_found: 'connections.error.not_found',
  timeout: 'connections.error.timeout',
  busy: 'connections.error.busy',
  blocked: 'connections.error.blocked',
  cancelled: 'connections.error.cancelled',
  failed: 'connections.error.failed',
  forbidden: 'connections.error.forbidden',
  network: 'connections.error.network',
};

export function errorKey(code: string | null | undefined): MessageKey {
  return ERROR_KEYS[(code ?? 'failed') as ConnectivityErrorCode] ?? 'connections.error.failed';
}

export interface Notice {
  id: string;
  tone: 'success' | 'error';
  key: MessageKey;
  vars?: Record<string, string>;
}

const SUCCESS_KEYS: Partial<Record<ConnectivityOperation['kind'], MessageKey>> = {
  wifi_connect: 'connections.done.wifiConnect',
  wifi_disconnect: 'connections.done.wifiDisconnect',
  wifi_forget: 'connections.done.wifiForget',
  bluetooth_pair: 'connections.done.btPair',
  bluetooth_connect: 'connections.done.btConnect',
  bluetooth_disconnect: 'connections.done.btDisconnect',
  bluetooth_forget: 'connections.done.btForget',
};

/**
 * What to tell the user when an operation finishes. Scans, radio toggles and
 * discovery are visible in the panel itself, so they only speak up on failure.
 */
export function noticeForOperation(op: ConnectivityOperation, targetLabel?: string): Notice | null {
  const vars = { target: targetLabel ?? op.target ?? '' };
  if (op.state === 'failed') {
    let key = errorKey(op.error?.code);
    if (op.error?.code === 'auth_failed') {
      key = op.kind.startsWith('wifi') ? 'connections.error.wifiAuth' : 'connections.error.btAuth';
    }
    return { id: op.id, tone: 'error', key, vars };
  }
  if (op.state === 'succeeded') {
    const key = SUCCESS_KEYS[op.kind];
    return key ? { id: op.id, tone: 'success', key, vars } : null;
  }
  return null;
}

export type WifiIndicatorState = 'off' | 'disconnected' | 'connecting' | 'connected';

export function wifiIndicator(wifi: WifiStatus | undefined): { state: WifiIndicatorState; bars: number } {
  if (!wifi || !wifi.available || !wifi.enabled) return { state: 'off', bars: 0 };
  if (wifi.state === 'connected') return { state: 'connected', bars: signalBars(wifi.signal) };
  if (wifi.state === 'connecting') return { state: 'connecting', bars: 0 };
  return { state: 'disconnected', bars: 0 };
}

export function bluetoothIndicator(bt: BluetoothStatus | undefined): { on: boolean; connected: number } {
  if (!bt || !bt.available || !bt.powered) return { on: false, connected: 0 };
  return { on: true, connected: bt.devices.filter((device) => device.connected).length };
}

export const INTERNET_KEYS: Record<InternetState, MessageKey> = {
  online: 'connections.internet.online',
  portal: 'connections.internet.portal',
  limited: 'connections.internet.limited',
  offline: 'connections.internet.offline',
  unknown: 'connections.internet.unknown',
};

export function splitDevices(devices: BluetoothDevice[]): { paired: BluetoothDevice[]; nearby: BluetoothDevice[] } {
  return {
    paired: devices.filter((device) => device.paired),
    nearby: devices.filter((device) => !device.paired),
  };
}

/** WPA passphrase rule mirrored from the server so the Connect button can disable early. */
export function isValidWifiPassword(password: string): boolean {
  if (/^[0-9a-fA-F]{64}$/.test(password)) return true;
  return password.length >= 8 && password.length <= 63 && /^[\x20-\x7e]*$/.test(password);
}

export const MAX_SSID_BYTES = 32;

export function isValidSsid(ssid: string): boolean {
  return ssid.length > 0 && new TextEncoder().encode(ssid).length <= MAX_SSID_BYTES;
}
