import config from '../../playwright.config';
export default {
 ...config, outputDir: '../../test-results/rx5', testDir: '.', testMatch: /(?:tablet-)?app-rx5\.spec\.ts$/, timeout: 600000, expect: { timeout: 30000 }, webServer: undefined,
 use: { ...config.use, baseURL: 'http://127.0.0.1:8055' },
 projects: config.projects!.map(p => ({ ...p, testMatch: p.name!.startsWith('Tablet') ? /tablet-app-rx5\.spec\.ts$/ : /app-rx5\.spec\.ts$/ })),
};
