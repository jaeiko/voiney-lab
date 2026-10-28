import { test } from './routes';
import { hood1Walk } from './app-hood1-walk';

test.use({
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  permissions: ['microphone'],
});

test('HOOD1: the final start press to a confirmed end is voice-only and far view keeps its measured type', async ({ page }) => {
  await hood1Walk(page);
});
