import { test } from './routes';
import { fieldWalk } from './app-rx4-walk';

// Lane RX4 (2026-10-26): app-rx4.spec.ts on a tablet, portrait and landscape (touch on).

test.use({
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  permissions: ['microphone'],
});
test.describe.configure({ timeout: 120_000 });

test('EAM 2.3 (chemistry, steps without numbers): the source at an ordinary size, a pause that resumes, the end skipped', async ({ page }, info) => {
  await fieldWalk(page, info, 'eam23');
});

test('Soil nutrient SOP 005 (chemistry / analysis, numbered source): the same', async ({ page }, info) => {
  await fieldWalk(page, info, 'soil');
});
