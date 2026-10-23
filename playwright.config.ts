import { defineConfig, devices } from '@playwright/test';

const PORT = process.env.VOINEY_LAB_PLAYWRIGHT_APP_PORT || '8000';
const BASE_URL = `http://127.0.0.1:${PORT}`;
// Lane TAB (2026-10-23): the CI config's two tablets (portrait 820x1180, landscape 1180x820; Chromium, touch on), which walk the tablet spec only.
const TABLET = { ...devices['Galaxy Tab S4'], deviceScaleFactor: 2 };
const TABLET_SPECS = /tablet-.*\.spec\.ts$/;

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'Desktop Chrome',
      testIgnore: TABLET_SPECS,
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'Mobile Chrome',
      testIgnore: TABLET_SPECS,
      use: { ...devices['Pixel 5'] },
    },
    {
      name: 'Tablet portrait',
      testMatch: TABLET_SPECS,
      use: { ...TABLET, viewport: { width: 820, height: 1180 }, screen: { width: 820, height: 1180 } },
    },
    {
      name: 'Tablet landscape',
      testMatch: TABLET_SPECS,
      use: { ...TABLET, viewport: { width: 1180, height: 820 }, screen: { width: 1180, height: 820 } },
    },
  ],
  webServer: {
    command: 'bash scripts/run_dev.sh',
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
