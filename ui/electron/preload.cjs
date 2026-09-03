// Preload script for the auto-update IPC bridge.
// Runs in the renderer's sandboxed context (sandbox: true).
// Uses CommonJS require() — this file is .cjs because ui/package.json
// sets "type": "module" which would otherwise treat .js as ESM.
//
// This file exposes ONLY the update API. No other Node/Electron APIs
// are forwarded to the renderer. The actual uv/npm work stays in
// main.js (and updater.js) and is never reachable from the network.

'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronUpdate', {
  /**
   * Fetches latest.json for the active channel and returns the status.
   * @returns {Promise<UpdateStatus>}
   */
  checkForUpdate: () => ipcRenderer.invoke('update:check'),

  /**
   * Starts the full apply pipeline (download → verify → extract → install → swap).
   * The caller must check session-idle state before invoking this.
   * @returns {Promise<void>}
   */
  applyUpdate: () => ipcRenderer.invoke('update:apply'),

  /**
   * Returns the active release channel ('stable' | 'experimental').
   * @returns {Promise<'stable'|'experimental'>}
   */
  getChannel: () => ipcRenderer.invoke('update:getChannel'),

  /**
   * Switches the active release channel and persists the preference to disk.
   * @param {'stable'|'experimental'} channel
   * @returns {Promise<'stable'|'experimental'>} the confirmed active channel
   */
  setChannel: (channel) => ipcRenderer.invoke('update:setChannel', channel),

  /**
   * Subscribe to status pushes from the main process.
   * @param {(status: UpdateStatus) => void} callback
   * @returns {() => void} unsubscribe function
   */
  onStatusChange: (callback) => {
    const handler = (_event, status) => callback(status);
    ipcRenderer.on('update:status', handler);
    return () => ipcRenderer.off('update:status', handler);
  },
});
