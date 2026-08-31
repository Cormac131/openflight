import { defineConfig, devices } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const PORT = 5173;
const HOST = '127.0.0.1';
const BASE_URL = `http://${HOST}:${PORT}`;
const CONFIG_DIR = fileURLToPath(new URL('.', import.meta.url));
// Learned club tags go to a scratch file, never the operator's real
// ~/.openflight/club_tags.json. OPENFLIGHT_NFC_MOCK is a test-only in-memory
// reader; --nfc on a real kiosk always opens the PN532, including with --mock.
const CLUB_TAGS_FILE = join(tmpdir(), 'openflight-e2e-club-tags.json');
const BACKEND_ARGS =
  `--mock --host ${HOST} --web-port 8080 --no-camera --no-logging ` +
  `--nfc --nfc-tags-file ${CLUB_TAGS_FILE}`;
const BACKEND_COMMAND =
  process.env.CI
    ? `python -m openflight.server ${BACKEND_ARGS}`
    : `uv run openflight-server ${BACKEND_ARGS}`;

export default defineConfig({
  testDir: './tests/e2e',
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
      reuseExistingServer: !process.env.CI,
      cwd: fileURLToPath(new URL('..', import.meta.url)),
      env: { ...process.env, OPENFLIGHT_NFC_MOCK: '1' },
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
