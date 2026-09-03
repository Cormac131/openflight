import { describe, it, expect, vi } from 'vitest';
import {
  isVersionNewer,
  bundleInstallPaths,
  createUpdater,
  UpdateState,
  ApplyStage,
  STABLE_FEED_URL,
  EXPERIMENTAL_FEED_URL,
} from './updater.js';
import { dirname } from 'node:path';
import { join } from 'node:path';

// ---------------------------------------------------------------------------
// isVersionNewer
// ---------------------------------------------------------------------------

describe('isVersionNewer', () => {
  it('returns false when versions are identical', () => {
    expect(isVersionNewer('v0.2.0', 'v0.2.0')).toBe(false);
  });

  it('returns true when versions differ', () => {
    expect(isVersionNewer('v0.2.0', 'v0.3.0')).toBe(true);
  });

  it('returns false when either value is falsy', () => {
    expect(isVersionNewer(null, 'v0.3.0')).toBe(false);
    expect(isVersionNewer('v0.2.0', null)).toBe(false);
    expect(isVersionNewer('', 'v0.3.0')).toBe(false);
    expect(isVersionNewer('v0.2.0', '')).toBe(false);
  });

  it('trims whitespace before comparing', () => {
    expect(isVersionNewer('v0.2.0\n', 'v0.2.0\n')).toBe(false);
    expect(isVersionNewer('v0.2.0\n', 'v0.3.0\n')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// bundleInstallPaths
// ---------------------------------------------------------------------------

describe('bundleInstallPaths', () => {
  it('returns .next and .prev siblings of the project root', () => {
    const root = join('/home', 'coleman', 'openflight');
    const paths = bundleInstallPaths(root);
    expect(paths.next).toBe(join(dirname(root), 'openflight.next'));
    expect(paths.prev).toBe(join(dirname(root), 'openflight.prev'));
  });

  it('works with nested paths', () => {
    const root = join('/srv', 'apps', 'openflight');
    const paths = bundleInstallPaths(root);
    expect(paths.next).toBe(join(dirname(root), 'openflight.next'));
    expect(paths.prev).toBe(join(dirname(root), 'openflight.prev'));
  });
});

// ---------------------------------------------------------------------------
// createUpdater — checkForUpdate
// ---------------------------------------------------------------------------

const FEED_URL = 'https://example.com/latest.json';

describe('createUpdater – checkForUpdate', () => {
  // Creates a temporary directory with a version.json and returns cleanup fn.
  async function withVersionRoot(version) {
    const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
    const { join: joinPath } = await import('node:path');
    const { tmpdir } = await import('node:os');
    const tmpRoot = mkdtempSync(joinPath(tmpdir(), 'of-test-'));
    writeFileSync(joinPath(tmpRoot, 'version.json'), JSON.stringify({ version }));
    return {
      root: tmpRoot,
      cleanup: () => rmSync(tmpRoot, { recursive: true, force: true }),
    };
  }

  function makeFeed(version) {
    return vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => ({
        version,
        url: 'https://example.com/bundle.tar.gz',
        checksum: 'abc123',
      }),
      body: null,
    }));
  }

  it('emits CHECKING then UP_TO_DATE when versions match', async () => {
    const { root, cleanup } = await withVersionRoot('v0.2.0');
    try {
      const fetchFn = makeFeed('v0.2.0');
      const updater = createUpdater({ projectRoot: root, feedUrl: FEED_URL, exec: vi.fn(), fetchFn });
      const statuses = [];
      updater.emitter.on('status', (s) => statuses.push(s));

      const result = await updater.checkForUpdate();

      expect(statuses[0].type).toBe(UpdateState.CHECKING);
      expect(result.type).toBe(UpdateState.UP_TO_DATE);
      expect(result.localVersion).toBe('v0.2.0');
    } finally { cleanup(); }
  });

  it('emits AVAILABLE when remote version differs', async () => {
    const { root, cleanup } = await withVersionRoot('v0.2.0');
    try {
      const fetchFn = makeFeed('v0.3.0');
      const updater = createUpdater({ projectRoot: root, feedUrl: FEED_URL, exec: vi.fn(), fetchFn });

      const result = await updater.checkForUpdate();

      expect(result.type).toBe(UpdateState.AVAILABLE);
      expect(result.localVersion).toBe('v0.2.0');
      expect(result.remoteVersion).toBe('v0.3.0');
    } finally { cleanup(); }
  });

  it('treats missing version.json as always available (git-checkout migration)', async () => {
    const { mkdtempSync, rmSync } = await import('node:fs');
    const { join: joinPath } = await import('node:path');
    const { tmpdir } = await import('node:os');
    const tmpRoot = mkdtempSync(joinPath(tmpdir(), 'of-test-'));
    // No version.json written.
    try {
      const fetchFn = makeFeed('v0.3.0');
      const updater = createUpdater({ projectRoot: tmpRoot, feedUrl: FEED_URL, exec: vi.fn(), fetchFn });

      const result = await updater.checkForUpdate();

      expect(result.type).toBe(UpdateState.AVAILABLE);
      expect(result.localVersion).toBeNull();
    } finally { rmSync(tmpRoot, { recursive: true, force: true }); }
  });

  it('emits ERROR when the feed returns a non-OK status', async () => {
    const { root, cleanup } = await withVersionRoot('v0.2.0');
    try {
      const fetchFn = vi.fn(async () => ({
        ok: false, status: 503, statusText: 'Service Unavailable',
        json: async () => ({}), body: null,
      }));
      const updater = createUpdater({ projectRoot: root, feedUrl: FEED_URL, exec: vi.fn(), fetchFn });
      const statuses = [];
      updater.emitter.on('status', (s) => statuses.push(s));

      const result = await updater.checkForUpdate();

      expect(result.type).toBe(UpdateState.ERROR);
      expect(result.error).toMatch('503');
    } finally { cleanup(); }
  });

  it('emits ERROR when the network is unreachable', async () => {
    const { root, cleanup } = await withVersionRoot('v0.2.0');
    try {
      const fetchFn = vi.fn(async () => { throw new Error('ECONNREFUSED'); });
      const updater = createUpdater({ projectRoot: root, feedUrl: FEED_URL, exec: vi.fn(), fetchFn });

      const result = await updater.checkForUpdate();

      expect(result.type).toBe(UpdateState.ERROR);
      expect(result.error).toMatch('ECONNREFUSED');
    } finally { cleanup(); }
  });

  it('returns current status immediately when a check is already running', async () => {
    const { root, cleanup } = await withVersionRoot('v0.2.0');
    try {
      const fetchFn = vi.fn(() => new Promise(() => {})); // never resolves
      const updater = createUpdater({ projectRoot: root, feedUrl: FEED_URL, exec: vi.fn(), fetchFn });

      const first = updater.checkForUpdate(); // starts, sets busy=true
      const secondResult = await updater.checkForUpdate(); // returns immediately

      expect(secondResult.type).toBe(UpdateState.CHECKING);
      first.then(() => {}); // suppress unhandled promise
    } finally { cleanup(); }
  });
});

// ---------------------------------------------------------------------------
// createUpdater — applyUpdate
// ---------------------------------------------------------------------------

describe('createUpdater – applyUpdate', () => {
  it('emits DOWNLOADING and VERIFYING before failing on checksum mismatch', async () => {
    const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
    const { join: joinPath } = await import('node:path');
    const { tmpdir } = await import('node:os');
    const tmpRoot = mkdtempSync(joinPath(tmpdir(), 'of-test-'));
    writeFileSync(joinPath(tmpRoot, 'version.json'), JSON.stringify({ version: 'v0.2.0' }));

    try {
      // The tarball body is an empty Web ReadableStream; its SHA-256 won't match.
      const emptyBody = new ReadableStream({ start(c) { c.close(); } });

      const fetchFn = vi.fn(async (url) => {
        if (url === FEED_URL) {
          return {
            ok: true, status: 200, statusText: 'OK',
            json: async () => ({
              version: 'v0.3.0',
              url: 'https://example.com/bundle.tar.gz',
              checksum: 'intentionally-wrong',
            }),
            body: null,
          };
        }
        return { ok: true, status: 200, statusText: 'OK', json: async () => ({}), body: emptyBody };
      });

      const updater = createUpdater({
        projectRoot: tmpRoot,
        feedUrl: FEED_URL,
        exec: vi.fn(async () => ({ stdout: '', stderr: '' })),
        fetchFn,
      });
      const stages = [];
      updater.emitter.on('status', (s) => stages.push(s));

      await updater.checkForUpdate(); // populate latestBundleInfo
      await updater.applyUpdate();

      const applyingStages = stages
        .filter((s) => s.type === UpdateState.APPLYING)
        .map((s) => s.stage);

      expect(applyingStages).toContain(ApplyStage.DOWNLOADING);
      expect(applyingStages).toContain(ApplyStage.VERIFYING);
      expect(stages.at(-1).type).toBe(UpdateState.BUILD_FAILED);
      expect(stages.at(-1).error).toMatch('Checksum mismatch');
    } finally {
      rmSync(tmpRoot, { recursive: true, force: true });
    }
  });

  it('emits BUILD_FAILED when the feed is unavailable during apply', async () => {
    const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
    const { join: joinPath } = await import('node:path');
    const { tmpdir } = await import('node:os');
    const tmpRoot = mkdtempSync(joinPath(tmpdir(), 'of-test-'));
    writeFileSync(joinPath(tmpRoot, 'version.json'), JSON.stringify({ version: 'v0.2.0' }));

    try {
      // Feed is down (latestBundleInfo not cached, so applyUpdate must fetch).
      const fetchFn = vi.fn(async () => ({
        ok: false, status: 503, statusText: 'Service Unavailable',
        json: async () => ({}), body: null,
      }));

      const updater = createUpdater({
        projectRoot: tmpRoot,
        feedUrl: FEED_URL,
        exec: vi.fn(),
        fetchFn,
      });
      const statuses = [];
      updater.emitter.on('status', (s) => statuses.push(s));

      await updater.applyUpdate(); // no prior checkForUpdate → must fetch feed

      expect(statuses.at(-1).type).toBe(UpdateState.BUILD_FAILED);
      expect(statuses.at(-1).error).toMatch('503');
    } finally {
      rmSync(tmpRoot, { recursive: true, force: true });
    }
  });

  it('returns current status immediately when apply is already running', async () => {
    const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
    const { join: joinPath } = await import('node:path');
    const { tmpdir } = await import('node:os');
    const tmpRoot = mkdtempSync(joinPath(tmpdir(), 'of-test-'));
    writeFileSync(joinPath(tmpRoot, 'version.json'), JSON.stringify({ version: 'v0.2.0' }));

    try {
      const fetchFn = vi.fn(() => new Promise(() => {})); // never resolves
      const updater = createUpdater({ projectRoot: tmpRoot, feedUrl: FEED_URL, exec: vi.fn(), fetchFn });

      const first = updater.applyUpdate(); // starts, sets busy=true
      const secondResult = await updater.applyUpdate(); // returns immediately

      expect(secondResult.type).toBe(UpdateState.APPLYING);
      first.then(() => {}); // suppress unhandled promise
    } finally {
      rmSync(tmpRoot, { recursive: true, force: true });
    }
  });
});

// ---------------------------------------------------------------------------
// createUpdater – channel management
// ---------------------------------------------------------------------------

describe('createUpdater – channel management', () => {
  it('defaults to the stable channel', () => {
    const updater = createUpdater({ projectRoot: '/tmp', fetchFn: vi.fn(), exec: vi.fn() });
    expect(updater.getChannel()).toBe('stable');
  });

  it('respects the channel option', () => {
    const updater = createUpdater({ projectRoot: '/tmp', channel: 'experimental', fetchFn: vi.fn(), exec: vi.fn() });
    expect(updater.getChannel()).toBe('experimental');
  });

  it('setChannel switches between stable and experimental', () => {
    const updater = createUpdater({ projectRoot: '/tmp', fetchFn: vi.fn(), exec: vi.fn() });
    updater.setChannel('experimental');
    expect(updater.getChannel()).toBe('experimental');
    updater.setChannel('stable');
    expect(updater.getChannel()).toBe('stable');
  });

  it('setChannel coerces unknown values to stable', () => {
    const updater = createUpdater({ projectRoot: '/tmp', channel: 'experimental', fetchFn: vi.fn(), exec: vi.fn() });
    updater.setChannel('nightly');  // not a valid channel
    expect(updater.getChannel()).toBe('stable');
  });

  it('checkForUpdate uses the stable feed URL when channel is stable', async () => {
    const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
    const { join: joinPath } = await import('node:path');
    const { tmpdir } = await import('node:os');
    const tmpRoot = mkdtempSync(joinPath(tmpdir(), 'of-channel-'));
    writeFileSync(joinPath(tmpRoot, 'version.json'), JSON.stringify({ version: 'v0.2.0' }));
    try {
      const fetchFn = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ version: 'v0.2.0', url: 'https://example.com/bundle.tar.gz', checksum: 'sha256:abc' }),
      });
      const updater = createUpdater({ projectRoot: tmpRoot, exec: vi.fn(), fetchFn });
      await updater.checkForUpdate();
      expect(fetchFn).toHaveBeenCalledWith(STABLE_FEED_URL, expect.any(Object));
    } finally {
      rmSync(tmpRoot, { recursive: true, force: true });
    }
  });

  it('checkForUpdate uses the experimental feed URL when channel is experimental', async () => {
    const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
    const { join: joinPath } = await import('node:path');
    const { tmpdir } = await import('node:os');
    const tmpRoot = mkdtempSync(joinPath(tmpdir(), 'of-channel-exp-'));
    writeFileSync(joinPath(tmpRoot, 'version.json'), JSON.stringify({ version: 'v0.2.0' }));
    try {
      const fetchFn = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ version: 'v0.3.0-beta.1', url: 'https://example.com/bundle.tar.gz', checksum: 'sha256:abc' }),
      });
      const updater = createUpdater({ projectRoot: tmpRoot, channel: 'experimental', exec: vi.fn(), fetchFn });
      await updater.checkForUpdate();
      expect(fetchFn).toHaveBeenCalledWith(EXPERIMENTAL_FEED_URL, expect.any(Object));
    } finally {
      rmSync(tmpRoot, { recursive: true, force: true });
    }
  });

  it('explicit feedUrl override takes precedence over channel', async () => {
    const OVERRIDE_URL = 'https://staging.example.com/latest.json';
    const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
    const { join: joinPath } = await import('node:path');
    const { tmpdir } = await import('node:os');
    const tmpRoot = mkdtempSync(joinPath(tmpdir(), 'of-channel-override-'));
    writeFileSync(joinPath(tmpRoot, 'version.json'), JSON.stringify({ version: 'v0.2.0' }));
    try {
      const fetchFn = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ version: 'v0.2.0', url: 'https://example.com/bundle.tar.gz', checksum: 'sha256:abc' }),
      });
      const updater = createUpdater({
        projectRoot: tmpRoot,
        channel: 'experimental',
        feedUrl: OVERRIDE_URL,
        exec: vi.fn(),
        fetchFn,
      });
      await updater.checkForUpdate();
      expect(fetchFn).toHaveBeenCalledWith(OVERRIDE_URL, expect.any(Object));
    } finally {
      rmSync(tmpRoot, { recursive: true, force: true });
    }
  });

  it('setChannel clears cached bundle info so the next check re-fetches', async () => {
    const { mkdtempSync, writeFileSync, rmSync } = await import('node:fs');
    const { join: joinPath } = await import('node:path');
    const { tmpdir } = await import('node:os');
    const tmpRoot = mkdtempSync(joinPath(tmpdir(), 'of-channel-clear-'));
    writeFileSync(joinPath(tmpRoot, 'version.json'), JSON.stringify({ version: 'v0.2.0' }));
    try {
      const stablePayload = { version: 'v0.2.1', url: 'https://example.com/stable.tar.gz', checksum: 'sha256:s' };
      const expPayload   = { version: 'v0.3.0-beta.1', url: 'https://example.com/exp.tar.gz', checksum: 'sha256:e' };
      let callCount = 0;
      const fetchFn = vi.fn().mockImplementation(() => {
        const payload = callCount === 0 ? stablePayload : expPayload;
        callCount++;
        return Promise.resolve({ ok: true, json: async () => payload });
      });
      const updater = createUpdater({ projectRoot: tmpRoot, exec: vi.fn(), fetchFn });

      // First check on stable channel.
      await updater.checkForUpdate();
      expect(updater.getStatus().type).toBe(UpdateState.AVAILABLE);
      expect(updater.getStatus().remoteVersion).toBe('v0.2.1');

      // Switch to experimental — cache must be cleared.
      updater.setChannel('experimental');

      // Second check fetches the experimental feed.
      await updater.checkForUpdate();
      expect(fetchFn).toHaveBeenCalledTimes(2);
      expect(fetchFn.mock.calls[1][0]).toBe(EXPERIMENTAL_FEED_URL);
    } finally {
      rmSync(tmpRoot, { recursive: true, force: true });
    }
  });
});
