import { test } from './routes';
import { hood1Walk } from './app-hood1-walk';

test.use({
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  permissions: ['microphone'],
});

for (const protocol of ['ingel', 'eam23', 'soil', 'battery'] as const) {
  test(`HOOD1: ${protocol} goes from the final start press to a confirmed end by voice`, async ({ page }) => {
    await hood1Walk(page, protocol);
  });
}
