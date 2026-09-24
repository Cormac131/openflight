import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConnectivityApiError, connectivityApi } from './connectivityApi';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('connectivityApi', () => {
  it('POSTs JSON to the server origin and returns the operation', async () => {
    const operation = { id: 'op-1', kind: 'wifi_connect', target: 'Cafe', state: 'pending', error: null };
    const fetchMock = vi.fn(async () => jsonResponse({ operation }, 202));
    vi.stubGlobal('fetch', fetchMock);

    await expect(connectivityApi.wifiConnect('Cafe', 'password1')).resolves.toEqual(operation);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('http://localhost:8080/api/system/wifi/connect');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(String(init.body))).toEqual({ ssid: 'Cafe', password: 'password1', hidden: false });
  });

  it('encodes Bluetooth addresses and pairing ids into the path', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ operation: {} }, 202));
    vi.stubGlobal('fetch', fetchMock);
    await connectivityApi.bluetoothDevice('AA:BB:CC:DD:EE:FF', 'pair');
    await connectivityApi.respondPairing('pair/1', true, '0000');
    const urls = fetchMock.mock.calls.map((call) => (call as unknown as [string])[0]);
    expect(urls[0]).toBe('http://localhost:8080/api/system/bluetooth/devices/AA%3ABB%3ACC%3ADD%3AEE%3AFF/pair');
    expect(urls[1]).toBe('http://localhost:8080/api/system/bluetooth/pairing/pair%2F1');
  });

  it('GETs status, optionally forcing a refresh', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({}));
    vi.stubGlobal('fetch', fetchMock);
    await connectivityApi.status(true);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('http://localhost:8080/api/system/connectivity?refresh=1');
    expect(init.method).toBe('GET');
    expect(init.body).toBeUndefined();
  });

  it('maps server error payloads to typed errors', async () => {
    vi.stubGlobal('fetch', async () => jsonResponse({ error: { code: 'forbidden', message: 'nope' } }, 403));
    const error = await connectivityApi.status().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ConnectivityApiError);
    expect(error).toMatchObject({ code: 'forbidden', status: 403 });
  });

  it('treats a missing endpoint (older server, mock server) as not_found', async () => {
    vi.stubGlobal('fetch', async () => new Response('<html>', { status: 404 }));
    await expect(connectivityApi.status()).rejects.toMatchObject({ code: 'not_found', status: 404 });
  });

  it('reports unreachable servers as network errors', async () => {
    vi.stubGlobal('fetch', async () => {
      throw new TypeError('Failed to fetch');
    });
    await expect(connectivityApi.wifiScan()).rejects.toMatchObject({ code: 'network', status: 0 });
  });
});
