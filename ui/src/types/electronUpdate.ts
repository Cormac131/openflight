// Type declarations for the auto-update IPC bridge exposed by
// ui/electron/preload.cjs via contextBridge.exposeInMainWorld.

export type UpdateChannel = 'stable' | 'experimental';

export type ApplyStage =
  | 'downloading'
  | 'verifying'
  | 'extracting'
  | 'installing'
  | 'swapping';

export type UpdateStatusType =
  | { type: 'idle' }
  | { type: 'checking' }
  | { type: 'upToDate'; localVersion?: string }
  | { type: 'available'; localVersion?: string; remoteVersion?: string }
  | { type: 'applying'; stage: ApplyStage }
  | { type: 'buildFailed'; error: string }
  | { type: 'ready' }
  | { type: 'restarting' }
  | { type: 'error'; error: string };

export interface ElectronUpdateBridge {
  checkForUpdate(): Promise<UpdateStatusType>;
  applyUpdate(): Promise<void>;
  getChannel(): Promise<UpdateChannel>;
  setChannel(channel: UpdateChannel): Promise<UpdateChannel>;
  onStatusChange(callback: (status: UpdateStatusType) => void): () => void;
}

declare global {
  interface Window {
    electronUpdate?: ElectronUpdateBridge;
  }
}
