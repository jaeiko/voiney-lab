import { test } from './routes';
import { settingsWalk, walk } from './app-rx2-walk';

// Lane RX2 (2026-10-25): the new screen from login to the end of an experiment on a tablet, portrait and
// landscape (touch on). The server is a stand-in (app-rx2.ts); Chromium's fake
// capture device is the microphone.

test.use({
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  permissions: ['microphone'],
});
test.describe.configure({ timeout: 120_000 });

test('login, the first guide, the start flow and an experiment to its end, through a lost connection', async ({ page }, info) => {
  await walk(page, info);
});

test('the settings save each choice and keep the filter off until the voice is checked', async ({ page }, info) => {
  await settingsWalk(page, info);
});

