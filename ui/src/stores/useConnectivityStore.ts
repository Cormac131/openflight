import { create } from 'zustand';
import { ConnectivityApiError, connectivityApi } from '../services/connectivityApi';
import type {
  ConnectionsTab,
  ConnectivityOperation,
  ConnectivitySnapshot,
  DesktopStatus,
  OperationKind,
  PairingRequest,
} from '../types/connectivity';
import { errorKey, noticeForOperation, type Notice } from '../utils/connectivity';

/** Ids of operations already finished, so a late 202 response cannot resurrect them. */
const FINISHED_MEMORY = 50;

interface ConnectivityState {
  /** `unavailable` hides the controls: remote browser, old server, or no backend. */
  availability: 'unknown' | 'available' | 'unavailable';
  snapshot: ConnectivitySnapshot | null;
  desktop: DesktopStatus | null;
  pending: Record<string, ConnectivityOperation>;
  finished: string[];
  notice: Notice | null;
  pairing: PairingRequest[];
  /** Which Connections tab is open (`null`: panel closed). */
  panelTab: ConnectionsTab | null;
  openPanel: (tab: ConnectionsTab) => void;
  closePanel: () => void;
  load: (refresh?: boolean) => Promise<void>;
  applySnapshot: (snapshot: ConnectivitySnapshot) => void;
  applyOperation: (op: ConnectivityOperation) => void;
  run: (start: () => Promise<ConnectivityOperation>) => Promise<boolean>;
  reportError: (error: unknown) => void;
  addPairing: (request: PairingRequest) => void;
  removePairing: (id: string) => void;
  dismissNotice: () => void;
  isPending: (kind: OperationKind, target?: string | null) => boolean;
}

export const useConnectivityStore = create<ConnectivityState>((set, get) => ({
  availability: 'unknown',
  snapshot: null,
  desktop: null,
  pending: {},
  finished: [],
  notice: null,
  pairing: [],
  panelTab: null,
  openPanel: (tab) => set({ panelTab: tab }),
  closePanel: () => set({ panelTab: null }),

  load: async (refresh = false) => {
    try {
      get().applySnapshot(await connectivityApi.status(refresh));
    } catch (error) {
      // 403 (remote browser), 404 (older server/mock server) or unreachable:
      // hide the controls rather than showing something that cannot work.
      if (error instanceof ConnectivityApiError && error.code === 'network' && get().snapshot) {
        return; // transient; keep the last known state
      }
      set({ availability: 'unavailable' });
    }
  },

  // The server snapshot is authoritative for Wi-Fi/Bluetooth-group operations
  // and pairing prompts, so a missed socket event cannot leave a stuck spinner.
  // Discovery is ungrouped (never listed), so it stays until its own event.
  applySnapshot: (snapshot) =>
    set((state) => {
      const pending: Record<string, ConnectivityOperation> = {};
      for (const op of snapshot.operations) {
        if (!state.finished.includes(op.id)) pending[op.id] = op;
      }
      for (const op of Object.values(state.pending)) {
        if (op.kind === 'bluetooth_discovery') pending[op.id] = op;
      }
      return {
        availability: 'available',
        snapshot,
        desktop: snapshot.desktop ?? state.desktop,
        pending,
        pairing: snapshot.pairing,
      };
    }),

  applyOperation: (op) =>
    set((state) => {
      if (op.state === 'pending') {
        if (state.finished.includes(op.id)) return {};
        return { pending: { ...state.pending, [op.id]: op } };
      }
      const pending = { ...state.pending };
      delete pending[op.id];
      const finished = [...state.finished, op.id].slice(-FINISHED_MEMORY);
      const label = labelForTarget(state.snapshot, op);
      const notice = noticeForOperation(op, label) ?? state.notice;
      return { pending, finished, notice };
    }),

  run: async (start) => {
    try {
      get().applyOperation(await start());
      return true;
    } catch (error) {
      get().reportError(error);
      return false;
    }
  },

  reportError: (error) => {
    const code = error instanceof ConnectivityApiError ? error.code : 'failed';
    set({ notice: { id: `error-${Date.now()}`, tone: 'error', key: errorKey(code) } });
  },

  addPairing: (request) => set((state) => ({ pairing: mergePairing(state.pairing, [request]) })),
  removePairing: (id) => set((state) => ({ pairing: state.pairing.filter((request) => request.id !== id) })),
  dismissNotice: () => set({ notice: null }),

  isPending: (kind, target) =>
    Object.values(get().pending).some((op) => op.kind === kind && (target === undefined || op.target === target)),
}));

function mergePairing(current: PairingRequest[], incoming: PairingRequest[]): PairingRequest[] {
  const byId = new Map(current.map((request) => [request.id, request]));
  for (const request of incoming) byId.set(request.id, request);
  return [...byId.values()];
}

/** Bluetooth ops target an address; show the device name instead. */
function labelForTarget(snapshot: ConnectivitySnapshot | null, op: ConnectivityOperation): string | undefined {
  if (!op.target || !op.kind.startsWith('bluetooth')) return undefined;
  return snapshot?.bluetooth.devices.find((device) => device.address === op.target)?.name;
}

/** Start an operation from an event handler (no hook needed). */
export function runOperation(start: () => Promise<ConnectivityOperation>): Promise<boolean> {
  return useConnectivityStore.getState().run(start);
}
