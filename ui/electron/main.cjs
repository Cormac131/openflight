'use strict';

// Kiosk shell for the OpenFlight React UI. Loads whatever URL the launcher
// script gives it (the startup splash, then the app itself once it
// navigates there) in a chromeless, fullscreen window — this replaces
// scripts/start-kiosk.sh's old system-browser detection (chromium-browser /
// chromium / google-chrome / firefox) with one pinned Chromium version.

const { app, BrowserWindow, Menu } = require('electron');

const DEFAULT_URL = 'http://localhost:8080';
const targetUrl = process.env.OPENFLIGHT_URL || process.argv[2] || DEFAULT_URL;

Menu.setApplicationMenu(null);

function createWindow() {
  const win = new BrowserWindow({
    kiosk: true,
    fullscreen: true,
    autoHideMenuBar: true,
    backgroundColor: '#000000',
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
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
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  app.quit();
});
