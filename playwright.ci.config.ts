import { defineConfig, devices } from '@playwright/test';

// Lane TAB (2026-10-23): two tablets, Chromium with touch on -- portrait 820x1180 and landscape 1180x820
// (an Android tablet's Chrome, no "Mobile" in its name). They walk the tablet spec only; the phone and the
// desktop keep every other spec, as before.
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
    baseURL: 'http://127.0.0.1:8000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'Desktop Chrome', testIgnore: TABLET_SPECS, use: { ...devices['Desktop Chrome'] } },
    { name: 'Mobile Chrome', testIgnore: TABLET_SPECS, use: { ...devices['Pixel 5'] } },
    { name: 'Tablet portrait', testMatch: TABLET_SPECS, use: { ...TABLET, viewport: { width: 820, height: 1180 }, screen: { width: 820, height: 1180 } } },
    { name: 'Tablet landscape', testMatch: TABLET_SPECS, use: { ...TABLET, viewport: { width: 1180, height: 820 }, screen: { width: 1180, height: 820 } } },
  ],
  webServer: {
    command: 'bash scripts/run_ci_server.sh',
    url: 'http://127.0.0.1:8000',
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
