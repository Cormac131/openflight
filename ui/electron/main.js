// Kiosk shell for the OpenFlight React UI. Loads whatever URL the launcher
// script gives it (the startup splash, then the app itself once it
// navigates there) in a chromeless, fullscreen window — this replaces
// scripts/start-kiosk.sh's old system-browser detection (chromium-browser /
// chromium / google-chrome / firefox) with one pinned Chromium version.

import { app, BrowserWindow, Menu, ipcMain } from 'electron';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolveTargetUrl } from './resolveTargetUrl.js';
import { createUpdater } from './updater.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Project root is two directories up from ui/electron/.
const PROJECT_ROOT = resolve(__dirname, '..', '..');

const targetUrl = resolveTargetUrl(process.env, process.argv);

Menu.setApplicationMenu(null);

// How long to wait after startup before the first update check, and the
// interval between subsequent checks.
const FIRST_CHECK_DELAY_MS = 30_000;
const CHECK_INTERVAL_MS = 30 * 60 * 1000; // 30 minutes

// ---------------------------------------------------------------------------
// Channel preference — persisted to {userData}/channel.json so the user's
// choice survives restarts.
// ---------------------------------------------------------------------------

function readChannelPref(userDataPath) {
  try {
    const { channel } = JSON.parse(
      readFileSync(join(userDataPath, 'channel.json'), 'utf8'),
    );
    return channel === 'experimental' ? 'experimental' : 'stable';
  } catch {
    return 'stable';
  }
}

function writeChannelPref(userDataPath, channel) {
  try {
    mkdirSync(userDataPath, { recursive: true });
    writeFileSync(
      join(userDataPath, 'channel.json'),
      JSON.stringify({ channel }),
    );
  } catch {
    // Ignore — preference is ephemeral if the disk is read-only.
  }
}

// ---------------------------------------------------------------------------

function createWindow() {
  const win = new BrowserWindow({
    kiosk: true,
    fullscreen: true,
    autoHideMenuBar: true,
    backgroundColor: '#000000',
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      // Exposes window.electronUpdate to the renderer via contextBridge.
      preload: join(__dirname, 'preload.cjs'),
    },
  });

  win.setMenuBarVisibility(false);
  // The kiosk shell only ever shows the OpenFlight UI itself; deny any
  // attempt (e.g. target="_blank" links) to pop a second window.
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.loadURL(targetUrl);

  win.on('closed', () => {
    app.quit();
  });

  // ---- auto-updater ----

  const userDataPath = app.getPath('userData');
  const savedChannel = readChannelPref(userDataPath);

  const updater = createUpdater({
    projectRoot: PROJECT_ROOT,
    channel: savedChannel,
    // feedUrl env var overrides the channel for staging / testing.
    feedUrl: process.env.OPENFLIGHT_FEED_URL,
    onRelaunch: () => {
      app.relaunch();
      app.exit(0);
    },
  });

  // Forward every status push from the updater to the renderer.
  updater.emitter.on('status', (status) => {
    if (!win.isDestroyed()) {
      win.webContents.send('update:status', status);
    }
  });

  // Renderer asks for a check (manual button in the menu).
  ipcMain.handle('update:check', () => updater.checkForUpdate());

  // Renderer asks to apply (user confirmed + session is idle).
  ipcMain.handle('update:apply', () => updater.applyUpdate());

  // Renderer queries the active channel on startup.
  ipcMain.handle('update:getChannel', () => updater.getChannel());

  // Renderer switches the channel (menu toggle).
  ipcMain.handle('update:setChannel', (_event, channel) => {
    const ch = channel === 'experimental' ? 'experimental' : 'stable';
    updater.setChannel(ch);
    writeChannelPref(userDataPath, ch);
    return ch;
  });

  // Periodic background check — first fires FIRST_CHECK_DELAY_MS after
  // startup so it does not compete with hardware initialisation.
  const firstTimer = setTimeout(() => {
    updater.checkForUpdate();
    const intervalTimer = setInterval(() => {
      updater.checkForUpdate();
    }, CHECK_INTERVAL_MS);

    // Clean up on window close so the timer does not fire after quit.
    win.on('closed', () => clearInterval(intervalTimer));
  }, FIRST_CHECK_DELAY_MS);

  win.on('closed', () => clearTimeout(firstTimer));

  return win;
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  app.quit();
});
