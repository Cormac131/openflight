export type ConnectionsTab = 'wifi' | 'bluetooth' | 'advanced';

/** Mirrors `openflight.connectivity` payloads (see `models.py` / `service.py`). */

export type ConnectivityErrorCode =
  | 'unsupported_platform'
  | 'disabled'
  | 'service_unavailable'
  | 'no_adapter'
  | 'permission_denied'
  | 'invalid_request'
  | 'unsupported_security'
  | 'auth_failed'
  | 'not_found'
  | 'timeout'
  | 'busy'
  | 'blocked'
  | 'cancelled'
  | 'failed'
  // Client-side only:
  | 'forbidden'
  | 'network';

export type WifiSecurity = 'open' | 'wpa-psk' | 'sae' | 'owe' | 'wep' | 'enterprise';

export interface WifiNetwork {
  ssid: string;
  signal: number;
  security: WifiSecurity;
  frequency_mhz: number | null;
  saved: boolean;
  active: boolean;
  supported: boolean;
  needs_password: boolean;
}

export interface WifiStatus {
  available: boolean;
  reason: ConnectivityErrorCode | null;
  enabled: boolean;
  interface: string | null;
  state: 'unavailable' | 'disconnected' | 'connecting' | 'connected';
  ssid: string | null;
  signal: number | null;
  ip4_address: string | null;
  scanning: boolean;
  networks: WifiNetwork[];
  saved_networks: string[];
}

export interface BluetoothDevice {
  address: string;
  name: string;
  icon: string | null;
  paired: boolean;
  trusted: boolean;
  connected: boolean;
  rssi: number | null;
}

export interface BluetoothStatus {
  available: boolean;
  reason: ConnectivityErrorCode | null;
  powered: boolean;
  discovering: boolean;
  adapter_name: string | null;
  devices: BluetoothDevice[];
}

export type InternetState = 'online' | 'portal' | 'limited' | 'offline' | 'unknown';

export interface NetworkStatus {
  local_network: boolean;
  internet: InternetState;
  source: string;
}

export type OperationKind =
  | 'wifi_scan'
  | 'wifi_connect'
  | 'wifi_disconnect'
  | 'wifi_forget'
  | 'wifi_enable'
  | 'bluetooth_power'
  | 'bluetooth_discovery'
  | 'bluetooth_pair'
  | 'bluetooth_connect'
  | 'bluetooth_disconnect'
  | 'bluetooth_forget';

export interface ConnectivityError {
  code: ConnectivityErrorCode;
  message: string;
}

export interface ConnectivityOperation {
  id: string;
  kind: OperationKind;
  target: string | null;
  state: 'pending' | 'succeeded' | 'failed';
  error: ConnectivityError | null;
}

export type PairingKind = 'confirm' | 'enter_passkey' | 'enter_pin' | 'display_passkey' | 'display_pin' | 'authorize';

export interface PairingRequest {
  id: string;
  address: string;
  name: string;
  kind: PairingKind;
  passkey: string | null;
  expires_in_s: number;
}

export interface DesktopStatus {
  available: boolean;
  reason: string | null;
  mode: 'kiosk' | 'desktop' | null;
}

export interface ConnectivitySnapshot {
  wifi: WifiStatus;
  bluetooth: BluetoothStatus;
  network: NetworkStatus;
  operations: ConnectivityOperation[];
  pairing: PairingRequest[];
  /** Present on REST responses only; socket pushes keep the last value. */
  desktop?: DesktopStatus;
}
