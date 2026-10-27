import config from './rx5.config';
export default { ...config, outputDir: '../../test-results/rx5-integrity', testMatch: /app-rx5-integrity\.spec\.ts$/, projects: [{ ...config.projects[0], testMatch: /app-rx5-integrity\.spec\.ts$/ }] };
