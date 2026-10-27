import { test } from './routes';
import { parityWalk, runToolsWalk } from './app-rx5-walk';
test.use({ launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] }, permissions: ['microphone'] });
test.describe.configure({ timeout: 600000 });

test('RX5 full detail, record actions, restart preview, prose and settings across three fields', async ({ page }, info) => parityWalk(page, info));
test('RX5 run settings and dropped-speech history keep the live run unchanged', async ({ page }, info) => runToolsWalk(page, info));
