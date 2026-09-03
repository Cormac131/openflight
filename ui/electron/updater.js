// On-Pi auto-update: fetch latest.json release feed → download tarball →
// SHA-256 verify → extract to openflight.next/ → install deps →
// side-by-side tree swap → systemd restart.
//
// Trust boundary: this module is only ever called from main.js IPC handlers.
// Nothing in the Flask server or the renderer can trigger exec calls here.
//
// Pure helper functions (isVersionNewer, bundleInstallPaths) are exported for
// unit tests and do not depend on child_process or the filesystem.

import { existsSync } from 'node:fs';
import { createReadStream } from 'node:fs';
import { createWriteStream } from 'node:fs';
import { rename, rm, mkdtemp, readFile, readdir, mkdir } from 'node:fs/promises';
import { join, dirname, basename } from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { EventEmitter } from 'node:events';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';

const execFileAsync = promisify(execFile);

// Default release feed; override via createUpdater({ feedUrl }) or
// the OPENFLIGHT_FEED_URL environment variable in main.js.
export const STABLE_FEED_URL =
  'https://github.com/Cormac131/openflight/releases/latest/download/latest.json';
export const EXPERIMENTAL_FEED_URL =
  'https://github.com/Cormac131/openflight/releases/download/experimental/latest-experimental.json';

// ---- update state types (mirrors UpdateStatusType in TypeScript) ----

export const UpdateState = Object.freeze({
  IDLE: 'idle',
  CHECKING: 'checking',
  UP_TO_DATE: 'upToDate',
  AVAILABLE: 'available',
  APPLYING: 'applying',
  BUILD_FAILED: 'buildFailed',
  READY: 'ready',
  RESTARTING: 'restarting',
  ERROR: 'error',
});

export const ApplyStage = Object.freeze({
  DOWNLOADING: 'downloading',
  VERIFYING: 'verifying',
  EXTRACTING: 'extracting',
  INSTALLING: 'installing',
  SWAPPING: 'swapping',
});

// ---- pure helpers (exported for unit tests) ----

/**
 * Returns true when remoteVersion differs from localVersion (simple string
 * equality after trim). Returns false if either value is falsy.
 */
export function isVersionNewer(localVersion, remoteVersion) {
  if (!localVersion || !remoteVersion) return false;
  return localVersion.trim() !== remoteVersion.trim();
}

/**
 * Returns the sibling directory paths used by the tree swap.
 * The .next and .prev directories sit beside the project root, so the
 * running checkout can be replaced without modifying its contents.
 */
export function bundleInstallPaths(projectRoot) {
  const parent = dirname(projectRoot);
  const name = basename(projectRoot);
  return {
    next: join(parent, `${name}.next`),
    prev: join(parent, `${name}.prev`),
  };
}

// ---- real exec (used in production; replaced by injection in tests) ----

async function defaultExec(file, args, opts = {}) {
  const { stdout = '', stderr = '' } = await execFileAsync(file, args, {
    timeout: opts.timeout ?? 300_000,
    // Inherit the full environment so PATH, HOME, UV_PYTHON, etc. are available.
    env: { ...process.env, ...opts.env },
    cwd: opts.cwd,
  });
  return { stdout: stdout.trim(), stderr: stderr.trim() };
}

// ---- file helpers ----

/** Streams a remote URL to a local file. Follows redirects (fetch handles them). */
async function downloadToFile(url, destPath, fetchFn) {
  const response = await fetchFn(url, { signal: AbortSignal.timeout(600_000) });
  if (!response.ok) {
    throw new Error(`Download failed: ${response.status} ${response.statusText}`);
  }
  const writer = createWriteStream(destPath);
  await pipeline(Readable.fromWeb(response.body), writer);
}

/** Returns the hex SHA-256 digest of a local file. */
async function sha256sum(filePath) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(filePath)) hash.update(chunk);
  return hash.digest('hex');
}

// ---- updater factory ----

/**
 * Creates an updater bound to a project root.
 *
 * Update flow:
 *   checkForUpdate() — fetches latest.json from the GitHub Releases feed and
 *     compares the `version` field against the local version.json.  If
 *     version.json is absent (git-checkout install), the update is always
 *     flagged as available so the Pi migrates to bundle installs.
 *
 *   applyUpdate() — download → SHA-256 verify → extract to openflight.next/ →
 *     uv sync --find-links wheels/ + npm ci --prefer-offline → side-by-side
 *     tree swap → systemd restart.
 *
 * @param {object}       options
 * @param {string}       options.projectRoot  Absolute path to the install directory.
 * @param {'stable'|'experimental'} [options.channel] Release channel ('stable' by default).
 * @param {string}       [options.feedUrl]    Explicit feed URL override (takes precedence over
 *                                            channel; useful for staging and tests).
 * @param {Function}     [options.exec]       Injectable exec; replaces execFile for tests.
 * @param {Function}     [options.fetchFn]    Injectable fetch; replaces globalThis.fetch for tests.
 * @param {Function}     [options.onRelaunch] Called to restart the Electron shell as a
 *                                            last-resort fallback (no-op if not provided).
 * @param {EventEmitter} [options.emitter]    Emits 'status' events consumed by IPC.
 */
export function createUpdater({
  projectRoot,
  channel = 'stable',
  feedUrl,                   // explicit override — wins over channel if set
  exec = defaultExec,
  fetchFn = globalThis.fetch,
  onRelaunch,
  emitter = new EventEmitter(),
} = {}) {
  let status = { type: UpdateState.IDLE };
  // Prevents concurrent check or apply operations.
  let busy = false;
  // Active release channel ('stable' | 'experimental').
  let currentChannel = channel === 'experimental' ? 'experimental' : 'stable';
  // Cached latest.json payload from checkForUpdate; consumed by applyUpdate
  // so we don't re-fetch between the user seeing "update available" and tapping
  // "apply".  Cleared after a successful apply or a channel change.
  let latestBundleInfo = null;

  function push(next) {
    status = next;
    emitter.emit('status', next);
  }

  /** Returns the feed URL for the active channel (or the explicit override). */
  function activeFeedUrl() {
    if (feedUrl) return feedUrl;
    return currentChannel === 'experimental' ? EXPERIMENTAL_FEED_URL : STABLE_FEED_URL;
  }

  /** Switch the active channel. Clears any cached feed payload. */
  function setChannel(ch) {
    const next = ch === 'experimental' ? 'experimental' : 'stable';
    if (next === currentChannel) return;
    currentChannel = next;
    latestBundleInfo = null;
  }

  /** Returns the active channel ('stable' | 'experimental'). */
  function getChannel() {
    return currentChannel;
  }

  // ------------------------------------------------------------------
  // checkForUpdate
  // Fetches latest.json and compares version strings.
  // Emits CHECKING → AVAILABLE | UP_TO_DATE | ERROR.
  // ------------------------------------------------------------------
  async function checkForUpdate() {
    if (busy) return status;
    busy = true;
    push({ type: UpdateState.CHECKING });
    try {
      const response = await fetchFn(activeFeedUrl(), { signal: AbortSignal.timeout(30_000) });
      if (!response.ok) {
        throw new Error(
          `Failed to fetch update feed: ${response.status} ${response.statusText}`
        );
      }
      const latest = await response.json();
      latestBundleInfo = latest;

      let localVersion = null;
      try {
        const versionJson = JSON.parse(
          await readFile(join(projectRoot, 'version.json'), 'utf8')
        );
        localVersion = versionJson.version ?? null;
      } catch {
        // No version.json — treat as unknown; show as available so the Pi
        // migrates from a git checkout to a bundle install.
      }

      const next =
        !localVersion || isVersionNewer(localVersion, latest.version)
          ? { type: UpdateState.AVAILABLE, localVersion, remoteVersion: latest.version }
          : { type: UpdateState.UP_TO_DATE, localVersion };

      push(next);
      return next;
    } catch (err) {
      const next = { type: UpdateState.ERROR, error: err.message ?? String(err) };
      push(next);
      return next;
    } finally {
      busy = false;
    }
  }

  // ------------------------------------------------------------------
  // applyUpdate
  // Download → verify → extract → install → tree swap → restart.
  // Emits APPLYING (with stage) → READY → RESTARTING  |  BUILD_FAILED.
  // ------------------------------------------------------------------
  async function applyUpdate() {
    if (busy) return status;
    busy = true;
    // Push immediately so a concurrent invocation (or a status poll) doesn't
    // see IDLE while the feed fetch / download is in progress.
    push({ type: UpdateState.APPLYING, stage: ApplyStage.DOWNLOADING });

    try {
      // Re-fetch latest.json if applyUpdate is called without a prior check.
      if (!latestBundleInfo) {
        const response = await fetchFn(activeFeedUrl(), { signal: AbortSignal.timeout(30_000) });
        if (!response.ok) {
          throw new Error(
            `Failed to fetch update feed: ${response.status} ${response.statusText}`
          );
        }
        latestBundleInfo = await response.json();
      }
      const { url, checksum } = latestBundleInfo;

      const tmpDir = await mkdtemp(join(tmpdir(), 'openflight-update-'));
      try {
        // 1. Download the release bundle (DOWNLOADING stage already pushed above).
        const bundlePath = join(tmpDir, 'bundle.tar.gz');
        await downloadToFile(url, bundlePath, fetchFn);

        // 2. Verify the SHA-256 checksum before touching the install tree.
        push({ type: UpdateState.APPLYING, stage: ApplyStage.VERIFYING });
        const actualChecksum = await sha256sum(bundlePath);
        if (actualChecksum !== checksum) {
          throw new Error(
            `Checksum mismatch — expected ${checksum}, got ${actualChecksum}. ` +
            'The download may be corrupted; the previous version is still running.'
          );
        }

        // 3. Extract into a temp sub-directory, then move the top-level entry
        //    to <parent>/openflight.next/ so the full install is prepared before
        //    any change is made to the running directory.
        push({ type: UpdateState.APPLYING, stage: ApplyStage.EXTRACTING });
        const extractDir = join(tmpDir, 'extract');
        await mkdir(extractDir);
        await exec('tar', ['-xzf', bundlePath, '-C', extractDir], { timeout: 120_000 });
        const [topLevelDir] = await readdir(extractDir);

        const paths = bundleInstallPaths(projectRoot);
        await rm(paths.next, { recursive: true, force: true });
        await rename(join(extractDir, topLevelDir), paths.next);

        // 4. Install Python and Node dependencies inside openflight.next/.
        //    Python: uv sync reads uv.lock; --find-links points at the bundled
        //            wheels so binary packages install without a compiler.  Any
        //            package not in the bundle falls back to PyPI.
        //    Node:   npm ci --prefer-offline uses ~/.npm cache; only packages
        //            that changed (e.g. a new Electron version) are downloaded.
        push({ type: UpdateState.APPLYING, stage: ApplyStage.INSTALLING });
        const wheelsDir = join(paths.next, 'wheels');
        await exec('uv', ['sync', `--find-links=${wheelsDir}`], {
          cwd: paths.next,
          timeout: 300_000,
        });
        await exec('npm', ['ci', '--prefer-offline'], {
          cwd: join(paths.next, 'ui'),
          timeout: 300_000,
        });

        // 5. Side-by-side tree swap:
        //      openflight        → openflight.prev
        //      openflight.next   → openflight
        //
        //    A crash between the two renames leaves the install directory absent.
        //    systemd will restart → fail; both .prev and .next survive for manual
        //    recovery: `mv ~/openflight.prev ~/openflight`.
        push({ type: UpdateState.APPLYING, stage: ApplyStage.SWAPPING });
        await rm(paths.prev, { recursive: true, force: true });
        if (existsSync(projectRoot)) {
          await rename(projectRoot, paths.prev);
        }
        await rename(paths.next, projectRoot);

        latestBundleInfo = null;
      } finally {
        // Best-effort; tmpdir is also flushed on reboot.
        rm(tmpDir, { recursive: true, force: true }).catch(() => {});
      }
    } catch (err) {
      // Clean up any partial .next directory so the next attempt starts fresh.
      const paths = bundleInstallPaths(projectRoot);
      await rm(paths.next, { recursive: true, force: true }).catch(() => {});
      push({ type: UpdateState.BUILD_FAILED, error: err.message ?? String(err) });
      busy = false;
      return status;
    }

    push({ type: UpdateState.READY });

    // Restart. We try the systemd unit first (restarts Python + main.js too).
    //   For passwordless sudo: add to /etc/sudoers.d/openflight:
    //     coleman ALL=(ALL) NOPASSWD: /bin/systemctl restart openflight
    //   Alternatively, install openflight.service as a user service and use
    //   `systemctl --user restart openflight` (no sudo needed).
    push({ type: UpdateState.RESTARTING });
    try {
      await exec('sudo', ['systemctl', 'restart', 'openflight'], { timeout: 15_000 });
    } catch {
      try {
        await exec('systemctl', ['--user', 'restart', 'openflight'], { timeout: 15_000 });
      } catch {
        // Last resort: restart the Electron shell only.
        onRelaunch?.();
      }
    }
    busy = false;
    return status;
  }

  return {
    checkForUpdate,
    applyUpdate,
    setChannel,
    getChannel,
    getStatus: () => status,
    emitter,
  };
}
