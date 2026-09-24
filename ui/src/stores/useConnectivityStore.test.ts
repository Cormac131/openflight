import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useConnectivityStore } from './useConnectivityStore';
import { ConnectivityApiError, connectivityApi } from '../services/connectivityApi';
import type { ConnectivityOperation, ConnectivitySnapshot, PairingRequest } from '../types/connectivity';

const snapshot = (overrides: Partial<ConnectivitySnapshot> = {}): ConnectivitySnapshot => ({
  wifi: {
    available: true,
    reason: null,
    enabled: true,
    interface: 'wlan0',
    state: 'connected',
    ssid: 'Home',
    signal: 70,
    ip4_address: '192.168.1.2',
    scanning: false,
    networks: [],
    saved_networks: ['Home'],
  },
  bluetooth: {
    available: true,
    reason: null,
    powered: true,
    discovering: false,
    adapter_name: 'pi',
    devices: [
      { address: 'AA:BB', name: 'Speaker', icon: null, paired: false, trusted: false, connected: false, rssi: -50 },
    ],
  },
  network: { local_network: true, internet: 'online', source: 'networkmanager' },
  operations: [],
  pairing: [],
  ...overrides,
});

const op = (overrides: Partial<ConnectivityOperation> = {}): ConnectivityOperation => ({
  id: 'op-1',
  kind: 'wifi_connect',
  target: 'Cafe',
  state: 'pending',
  error: null,
  ...overrides,
});

const pairing: PairingRequest = {
  id: 'pair-1',
  address: 'AA:BB',
  name: 'Speaker',
  kind: 'confirm',
  passkey: '123456',
  expires_in_s: 25,
};

const initial = useConnectivityStore.getState();

beforeEach(() => {
  useConnectivityStore.setState(initial, true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useConnectivityStore', () => {
  it('tracks an operation from pending to a success notice', () => {
    const store = useConnectivityStore.getState();
    store.applyOperation(op());
    expect(useConnectivityStore.getState().isPending('wifi_connect', 'Cafe')).toBe(true);
    store.applyOperation(op({ state: 'succeeded' }));
    const state = useConnectivityStore.getState();
    expect(state.isPending('wifi_connect')).toBe(false);
    expect(state.notice).toMatchObject({ tone: 'success', key: 'connections.done.wifiConnect' });
  });

  it('never resurrects an operation whose result arrived before its 202', () => {
    const store = useConnectivityStore.getState();
    store.applyOperation(op({ state: 'failed', error: { code: 'auth_failed', message: '' } }));
    store.applyOperation(op()); // late HTTP response
    expect(useConnectivityStore.getState().pending).toEqual({});
    expect(useConnectivityStore.getState().notice?.key).toBe('connections.error.wifiAuth');
  });

  it('uses the device name for Bluetooth notices', () => {
    useConnectivityStore.getState().applySnapshot(snapshot());
    useConnectivityStore.getState().applyOperation(op({ kind: 'bluetooth_pair', target: 'AA:BB', state: 'succeeded' }));
    expect(useConnectivityStore.getState().notice?.vars).toEqual({ target: 'Speaker' });
  });

  it('treats snapshots as authoritative for grouped ops and pairing prompts', () => {
    const store = useConnectivityStore.getState();
    store.applyOperation(op()); // its finish event was missed
    store.applyOperation(op({ id: 'op-2', kind: 'bluetooth_discovery', target: null }));
    store.addPairing(pairing);
    store.applySnapshot(snapshot());
    const state = useConnectivityStore.getState();
    expect(Object.keys(state.pending)).toEqual(['op-2']);
    expect(state.pairing).toEqual([]);
    expect(state.availability).toBe('available');
  });

  it('keeps the last desktop status when a socket push omits it', () => {
    const store = useConnectivityStore.getState();
    store.applySnapshot(snapshot({ desktop: { available: true, reason: null, mode: 'kiosk' } }));
    store.applySnapshot(snapshot());
    expect(useConnectivityStore.getState().desktop?.available).toBe(true);
  });

  it('adds and removes pairing prompts', () => {
    const store = useConnectivityStore.getState();
    store.addPairing(pairing);
    store.addPairing(pairing);
    expect(useConnectivityStore.getState().pairing).toHaveLength(1);
    store.removePairing('pair-1');
    expect(useConnectivityStore.getState().pairing).toEqual([]);
  });

  it('hides the controls when the server refuses this browser', async () => {
    vi.spyOn(connectivityApi, 'status').mockRejectedValue(new ConnectivityApiError('forbidden', 403, 'no'));
    await useConnectivityStore.getState().load();
    expect(useConnectivityStore.getState().availability).toBe('unavailable');
  });

  it('keeps the last state through a transient network error', async () => {
    useConnectivityStore.getState().applySnapshot(snapshot());
    vi.spyOn(connectivityApi, 'status').mockRejectedValue(new ConnectivityApiError('network', 0, 'down'));
    await useConnectivityStore.getState().load();
    expect(useConnectivityStore.getState().availability).toBe('available');
  });

  it('turns a synchronous API rejection into an error notice', async () => {
    const started = await useConnectivityStore
      .getState()
      .run(() => Promise.reject(new ConnectivityApiError('busy', 409, 'busy')));
    expect(started).toBe(false);
    expect(useConnectivityStore.getState().notice).toMatchObject({ tone: 'error', key: 'connections.error.busy' });
  });

  it('never stores a Wi-Fi password', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ operation: op() }), { status: 202 }));
    vi.stubGlobal('fetch', fetchMock);
    await useConnectivityStore.getState().run(() => connectivityApi.wifiConnect('Cafe', 'super-secret-pw'));
    vi.unstubAllGlobals();
    expect(JSON.stringify(useConnectivityStore.getState())).not.toContain('super-secret-pw');
    expect(useConnectivityStore.getState().isPending('wifi_connect', 'Cafe')).toBe(true);
  });
});
