# Electron Kiosk Shell

`scripts/start-kiosk.sh` launches the React UI inside Electron
(`ui/electron/main.js`) rather than shelling out to whatever browser
happens to be installed on the Pi. This document explains why that's an
improvement, and sketches how it could support self-updating later. It does
not describe anything implemented yet beyond the shell itself — see
[Auto-Updates (Future Work)](#auto-updates-future-work).

## Why Electron Instead Of A System Browser

The old `launch_kiosk_browser` tried `chromium-browser`, then `chromium`,
then `google-chrome`, then `firefox` — whichever the OS image happened to
have, with `--kiosk` flags tuned mostly for Chromium. That worked, but it
carried a few risks an Electron shell removes:

| Concern | System browser | Electron shell |
|---|---|---|
| Rendering engine version | Whatever `apt` installed/upgraded on that Pi — can silently drift between units or after an OS update | Pinned in `ui/package-lock.json` (`electron@44.1.0` today), identical across every Pi until deliberately bumped |
| Kiosk lockdown | `--kiosk` behaves differently across Chromium, Chrome, and Firefox; Firefox's kiosk mode in particular is looser (menu/shortcuts still reachable) | One `BrowserWindow` with `kiosk: true`, no application menu, and `setWindowOpenHandler` denying any popup — the same guarantees everywhere |
| Startup noise | Chromium's "restore previous session" / crash bubbles needed extra flags (`--disable-session-crashed-bubble`) to suppress | Electron starts a fresh profile each launch; there's no session-restore prompt to suppress |
| Maintenance surface | A 4-branch `if/elif` detection ladder to keep working across Raspberry Pi OS Bookworm/Bullseye, Lite/Desktop images | One binary, one launch path; `npm ci` makes the exact runtime reproducible in CI the same way any other dependency is |
| Extensibility | A browser tab is sandboxed from the OS — no filesystem, process, or native API access | The Electron **main process** is a regular Node.js process with full OS access, which is what makes [self-updating](#auto-updates-future-work) possible at all |

The old detection ladder is kept as a fallback (`launch_kiosk_browser` still
tries `chromium-browser`/`chromium` if `ui/node_modules/.bin/electron` is
missing), so a Pi that hasn't run `npm install` yet doesn't lose its kiosk
entirely — it just loses the guarantees above until Electron is installed.

## What Didn't Change

Electron here is a shell, not a rewrite: `ui/electron/main.js` opens a
`BrowserWindow` and points it at the same URL the browser used to load
(`http://localhost:8080`, served by Flask from `ui/dist`). The React app,
the WebSocket connection (`socketService.ts`), and the Flask server are
untouched — `getServerOrigin()` still resolves to `window.location.origin`,
which is the Electron window's origin now instead of a browser tab's.

## Auto-Updates

OpenFlight can update itself from GitHub without an SSH session. The updater
runs entirely inside the Electron **main process** — no Flask endpoint, no
socket event, nothing reachable from the network can trigger it.

There are two separate things that can be "updated," and they use different
mechanisms.

### 1. UI content (the React build) — already effectively live

Electron loads a URL, not a bundled copy of `ui/dist`. Whatever Flask is
currently serving is what the window shows. So once a Pi has pulled a new
`ui/dist` (via the existing `git pull && npm run build` flow in
[splash-screen.md](splash-screen.md#updating-an-existing-pi)) and the
service restarts, the Electron window shows the new UI on its next launch —
no Electron-specific update logic needed for this layer. This is already
true today.

### 2. The Electron shell itself

`electron` is a normal `devDependency` in `ui/package.json`. Bumping its
version is a normal dependency bump: change the version, `npm install`,
commit the updated lockfile, `git pull` on each Pi. No runtime auto-update
machinery is needed for this either, as long as updates continue to arrive
through `git pull` + reinstall rather than an out-of-band download.

Installing that package (not running the Electron binary) needs **Node.js
22.12+** on the Pi. Node 20 prints `npm WARN EBADENGINE` for `electron@44`
and its `@electron/get` helper. See the Node install step in
[raspberry-pi-setup.md](raspberry-pi-setup.md).

### 3. The interesting case: OpenFlight self-updating without an SSH session

The capability an Electron main process adds that a browser tab never had
is **the kiosk can update itself**, because `main.js` runs as a full
Node.js process on the Pi rather than inside a sandboxed tab. Two designs,
in increasing order of complexity:

### 3. Self-updating without an SSH session

The updater polls a GitHub Releases feed, downloads a pre-built release bundle,
verifies its integrity, and swaps it in while the kiosk is running.

**How it works:**

1. Every 30 minutes (and 30 seconds after startup), `main.js` calls
   `updater.checkForUpdate()` — fetches `latest.json` from GitHub Releases and
   compares its `version` field against the local `version.json`. The renderer
   can also trigger a check manually via the Settings menu.
2. When a new version is detected, the menu shows **Update available** and an
   **Apply update** button.
3. The button is disabled while a shot is being processed
   (`shotProcessingPhase !== null`) so updates never interrupt a round.
4. The user taps **Apply update**. The main process runs the bundle pipeline:
   - **Downloading** — streams the release `.tar.gz` to a temp directory.
   - **Verifying** — SHA-256 check against `latest.json`; aborts on mismatch.
   - **Extracting** — `tar -xzf` into temp; top-level entry moved to
     `~/openflight.next/`.
   - **Installing** — `uv sync --find-links=wheels/` (uses pre-downloaded aarch64
     wheels from the bundle; falls back to PyPI if a wheel is missing)
     + `npm ci --prefer-offline` (uses the global `~/.npm` cache).
   - **Swapping** — `~/openflight` → `~/openflight.prev`,
     `~/openflight.next` → `~/openflight`. `openflight.prev` is kept for
     one-step manual recovery.
5. Restart: tries `sudo systemctl restart openflight`, then
   `systemctl --user restart openflight`, then `app.relaunch()` as a last resort.
6. If any step fails, the UI shows **Update failed** and the previous version
   continues running unchanged.

> **Note for git-checkout installs (no `version.json`):** the updater treats the
> version as unknown and always shows the update as available, prompting a
> migration to the bundle install path.

**CI release workflow** (`.github/workflows/release.yml`):

Triggered by any `v*` tag push (e.g. `v0.3.0`) on `ubuntu-latest`:

1. `npm ci && npm run build` → pre-built `ui/dist/`.
2. `pip download --platform linux_aarch64` → pre-fetches binary Python wheels.
3. Assembles bundle: Python source, `ui/dist/`, Electron files, `wheels/`,
   `version.json` manifest.
4. Uploads `openflight-v*-linux-arm64.tar.gz` and `latest.json` as GitHub
   Release assets. Stable feed URL:
   `https://github.com/Cormac131/openflight/releases/latest/download/latest.json`

**Experimental channel** (`.github/workflows/experimental.yml`):

Triggered by pre-release version tags (`v*-*`, e.g. `v0.3.0-beta.1`):

1. Same build steps as the stable workflow.
2. Bundle is named `openflight-experimental-linux-arm64.tar.gz` (fixed name so
   it overwrites the previous experimental build in-place).
3. Uploads the bundle and `latest-experimental.json` to a mutable GitHub Release
   with tag `experimental` (marked pre-release). Stable feed URL:
   `https://github.com/Cormac131/openflight/releases/download/experimental/latest-experimental.json`

**Switching channels on the Pi:**

Open the menu (logo button) → **Updates** → **Channel** → choose
**Stable** or **Experimental**. The preference is saved to
`{userData}/channel.json` and survives restarts. The next periodic check (or a
manual **Check for updates** tap) fetches from the new channel's feed.

**Override the feed URL** for staging: set
`OPENFLIGHT_FEED_URL=https://...` in the Electron process environment (overrides
the channel setting).

**Key modules:**

| File | Purpose |
|---|---|
| `ui/electron/updater.js` | Download/verify/swap pipeline (injectable for tests) |
| `ui/electron/preload.cjs` | `contextBridge` IPC bridge to the renderer |
| `ui/electron/main.js` | IPC handlers + periodic check timer |
| `ui/src/stores/useUpdateStore.ts` | Zustand store for update status in the React UI |
| `ui/src/components/UpdateDialog.tsx` | Full-screen overlay during apply / on failure |
| `ui/src/components/panel/MenuSheet.tsx` | Updates section (check + apply buttons) |

**Pi setup for passwordless systemd restart:**

```bash
sudo visudo -f /etc/sudoers.d/openflight-update
# Add:
coleman ALL=(ALL) NOPASSWD: /bin/systemctl restart openflight
```

Alternatively, install the service as a user service (`systemctl --user`) — no
sudo entry needed.

**Trust boundary:** the IPC channel is exposed only to the local renderer via
`contextBridge`. Nothing in the Flask server or the WebSocket API can trigger
downloads, `uv sync`, or filesystem swaps. The Pi's network clients have no path
to these operations.
