import { create } from 'zustand';
import type { HardwareStatus } from '../types/hardware';
import { UNKNOWN_HARDWARE_STATUS } from '../types/hardware';
import type { PowerStatus } from '../types/power';
import type { SimShotInfo, SimStatus } from '../types/socket';

interface SystemState {
  connected: boolean;
  mockMode: boolean;
  debugMode: boolean;
  cloudUploadState: 'idle' | 'running' | 'complete' | 'error';
  cloudUploadMessage: string;
  simStatuses: Record<string, SimStatus>;
  latestSimShots: Record<string, SimShotInfo>;
  serverClub: string | null;
  serverPlayerName: string | null;
  powerStatus: PowerStatus | null;
  /** Hardware that failed to start. See types/hardware.ts. */
  hardwareStatus: HardwareStatus;
  /** Degraded faults the owner has waved away this session. */
  dismissedFaults: string[];
  setConnected: (connected: boolean) => void;
  setMockMode: (mockMode: boolean) => void;
  setDebugMode: (debugMode: boolean) => void;
  setCloudUploadStatus: (state: SystemState['cloudUploadState'], message: string) => void;
  setSimStatus: (status: SimStatus) => void;
  setLatestSimShot: (shot: SimShotInfo) => void;
  setServerClub: (club: string | null) => void;
  setServerPlayerName: (playerName: string | null) => void;
  setPowerStatus: (status: PowerStatus) => void;
  setHardwareStatus: (status: HardwareStatus) => void;
  dismissFault: (device: string) => void;
}

export const useSystemStore = create<SystemState>((set) => ({
  connected: false,
  mockMode: false,
  debugMode: false,
  cloudUploadState: 'idle',
  cloudUploadMessage: '',
  simStatuses: {},
  latestSimShots: {},
  serverClub: null,
  serverPlayerName: null,
  powerStatus: null,
  hardwareStatus: UNKNOWN_HARDWARE_STATUS,
  dismissedFaults: [],
  setConnected: (connected) => set({ connected }),
  setMockMode: (mockMode) => set({ mockMode }),
  setDebugMode: (debugMode) => set({ debugMode }),
  setCloudUploadStatus: (cloudUploadState, cloudUploadMessage) => set({ cloudUploadState, cloudUploadMessage }),
  setSimStatus: (status) =>
    set((state) => ({
      simStatuses: { ...state.simStatuses, [status.target]: status },
    })),
  setLatestSimShot: (shot) =>
    set((state) => ({
      latestSimShots: { ...state.latestSimShots, [shot.target]: shot },
    })),
  setServerClub: (serverClub) => set({ serverClub }),
  setServerPlayerName: (serverPlayerName) => set({ serverPlayerName }),
  setPowerStatus: (status) => set({ powerStatus: status }),
  setHardwareStatus: (status) =>
    set((state) => ({
      hardwareStatus: status,
      // Drop dismissals for faults that have cleared, so if the same device
      // fails again later the owner is told again rather than silently.
      dismissedFaults: state.dismissedFaults.filter((device) =>
        status.faults.some((fault) => fault.device === device)
      ),
    })),
  dismissFault: (device) =>
    set((state) =>
      state.dismissedFaults.includes(device)
        ? state
        : { dismissedFaults: [...state.dismissedFaults, device] }
    ),
}));
