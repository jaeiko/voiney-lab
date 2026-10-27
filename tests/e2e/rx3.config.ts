// Local RX3 measurement: the brief explicitly requests four-device Playwright.
// Start the isolated server on 8033 with NEW_UI=true; no launcher or .env is read.
import config from '../../playwright.config';
export default {
  ...config, testDir: '.', testMatch: /(?:tablet-)?app-rx3\.spec\.ts$/,
  timeout: 180000, expect: { timeout: 30000 }, webServer: undefined,
  use: { ...config.use, baseURL: 'http://127.0.0.1:8033' },
  projects: config.projects!.map(project => ({ ...project, testMatch: project.name!.startsWith('Tablet') ? /tablet-app-rx3\.spec\.ts$/ : /app-rx3\.spec\.ts$/ })),
};
