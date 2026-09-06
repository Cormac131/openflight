import { defineConfig, devices } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PROFILES_PATH_ENV, uniqueE2eProfilesPath } from './tests/e2e/isolateProfilesPath';

const PORT = 5173;
const HOST = '127.0.0.1';
const BASE_URL = `http://${HOST}:${PORT}`;
const CONFIG_DIR = fileURLToPath(new URL('.', import.meta.url));
const BACKEND_ARGS = `--mock --host ${HOST} --web-port 8080 --no-camera --no-logging`;
const BACKEND_COMMAND = process.env.CI
  ? `python -m openflight.server ${BACKEND_ARGS}`
  : `uv run openflight-server ${BACKEND_ARGS}`;

const E2E_PROFILES_PATH = uniqueE2eProfilesPath();
// An empty updater directory makes the real server report an unmanaged install.
const E2E_UPDATE_DIR = mkdtempSync(join(tmpdir(), 'openflight-e2e-update-'));

function backendEnv(): { [key: string]: string } {
  const env: { [key: string]: string } = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined) env[key] = value;
  }
  env[PROFILES_PATH_ENV] = E2E_PROFILES_PATH;
  env.OPENFLIGHT_INSTALL_LINK = join(E2E_UPDATE_DIR, 'openflight');
  env.OPENFLIGHT_RELEASES_ROOT = join(E2E_UPDATE_DIR, 'releases');
  env.OPENFLIGHT_UPDATE_CONFIG = join(E2E_UPDATE_DIR, 'update.json');
  env.OPENFLIGHT_UPDATE_STATUS = join(E2E_UPDATE_DIR, 'update-status.json');
  return env;
}

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['html', { outputFolder: 'playwright-report', open: 'never' }], ['github']] : 'list',
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: [
    {
      command: BACKEND_COMMAND,
      url: `http://${HOST}:8080`,
      reuseExistingServer: false,
      cwd: fileURLToPath(new URL('..', import.meta.url)),
      env: backendEnv(),
    },
    {
      command: `npm run dev -- --host ${HOST} --port ${PORT} --mode test`,
      url: BASE_URL,
      reuseExistingServer: !process.env.CI,
      cwd: CONFIG_DIR,
    },
  ],
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
