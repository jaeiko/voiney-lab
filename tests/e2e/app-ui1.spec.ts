import { test } from './routes';
import { ui1Records, ui1Walk } from './app-ui1-walk';

// Lane UI1 (2026-10-31): the experiment screen in halves (a desktop) or one column (a phone), the end screen's shared
// controls and the records list in the lab's time zone, on protocols of three fields. tablet-app-ui1.spec.ts walks
// the same on the two tablets.

test.use({
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  permissions: ['microphone'],
});
test.describe.configure({ timeout: 120_000 });

test('in-gel digestion (biology): the source figure and a running timer beside the step', async ({ page }, info) => {
  await ui1Walk(page, info, 'ingel');
});

test('EAM 2.3 (chemistry): a web photo with its credit and a timer in its last minute', async ({ page }, info) => {
  await ui1Walk(page, info, 'eam23');
});

test('battery electrode set (materials): a drawn diagram, no timer', async ({ page }, info) => {
  await ui1Walk(page, info, 'battery');
});

test('the records list: the run start in Seoul time, newest first, the unconfirmed badge', async ({ page }, info) => {
  await ui1Records(page, info);
});
