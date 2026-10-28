import { defineConfig } from '@playwright/test';
import { join } from 'node:path';
import { homedir } from 'node:os';

// Deliberately separate from the CI config: no product launcher, .env or runtime store.
export default defineConfig({
  testDir: '.', testMatch: 'readme-capture.spec.ts', workers: 1, retries: 0,
  timeout: 90_000, reporter: 'list', outputDir: join(homedir(), 'reports', 'readme-capture', 'results'),
  use: {
    baseURL: 'http://127.0.0.1:8046', browserName: 'chromium',
    deviceScaleFactor: 1, isMobile: true, hasTouch: true,
    permissions: ['microphone'], serviceWorkers: 'block',
    launchOptions: { args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] },
  },
  projects: [
    { name: 'tablet', use: { viewport: { width: 1180, height: 820 } } },
    { name: 'phone', use: { viewport: { width: 412, height: 915 } } },
  ],
  webServer: {
    command: '.venv/bin/python -m http.server 8046 --bind 127.0.0.1 --directory src/voiney_lab/static',
    cwd: '../..', url: 'http://127.0.0.1:8046/app/', reuseExistingServer: false,
  },
});
