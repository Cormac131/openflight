import type { ConnectivityErrorCode, ConnectivityOperation, ConnectivitySnapshot } from '../types/connectivity';
import { getServerOrigin } from '../utils/serverOrigin';

/**
 * REST client for `/api/system`. Requests go straight to the OpenFlight
 * server origin (not the Vite proxy) so the server's local-device check sees
 * the real peer address. The server refuses these calls from other devices.
 */
export class ConnectivityApiError extends Error {
  readonly code: ConnectivityErrorCode;
  readonly status: number;

  constructor(code: ConnectivityErrorCode, status: number, message: string) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

type Fetch = typeof fetch;

async function request<T>(path: string, body?: unknown, fetchImpl: Fetch = fetch): Promise<T> {
  let response: Response;
  try {
    response = await fetchImpl(`${getServerOrigin()}/api/system${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: 'no-store',
    });
  } catch {
    throw new ConnectivityApiError('network', 0, 'Could not reach OpenFlight');
  }
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const error = (payload as { error?: { code?: string; message?: string } } | null)?.error;
    const code =
      (error?.code as ConnectivityErrorCode | undefined) ?? (response.status === 404 ? 'not_found' : 'failed');
    throw new ConnectivityApiError(code, response.status, error?.message ?? response.statusText);
  }
  return payload as T;
}

type OperationResponse = { operation: ConnectivityOperation };

async function operation(path: string, body: unknown = {}, fetchImpl?: Fetch): Promise<ConnectivityOperation> {
  return (await request<OperationResponse>(path, body, fetchImpl)).operation;
}

export const connectivityApi = {
  status: (refresh = false, fetchImpl?: Fetch) =>
    request<ConnectivitySnapshot>(`/connectivity${refresh ? '?refresh=1' : ''}`, undefined, fetchImpl),
  wifiScan: () => operation('/wifi/scan'),
  wifiSetEnabled: (enabled: boolean) => operation('/wifi/enabled', { enabled }),
  /** The password is sent once and never kept by the client after this call. */
  wifiConnect: (ssid: string, password: string | null, hidden = false, fetchImpl?: Fetch) =>
    operation('/wifi/connect', { ssid, password, hidden }, fetchImpl),
  wifiDisconnect: () => operation('/wifi/disconnect'),
  wifiForget: (ssid: string) => operation('/wifi/forget', { ssid }),
  bluetoothPower: (enabled: boolean) => operation('/bluetooth/power', { enabled }),
  bluetoothDiscovery: (enabled: boolean) => operation('/bluetooth/discovery', { enabled }),
  bluetoothDevice: (address: string, action: 'pair' | 'connect' | 'disconnect' | 'forget') =>
    operation(`/bluetooth/devices/${encodeURIComponent(address)}/${action}`),
  respondPairing: (id: string, accept: boolean, value: string | null = null) =>
    request<{ status: string }>(`/bluetooth/pairing/${encodeURIComponent(id)}`, { accept, value }),
  showDesktop: () => request<{ status: string }>('/desktop/show', {}),
};
