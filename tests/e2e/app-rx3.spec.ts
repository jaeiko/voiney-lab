import { test } from './routes';
import { deskControls, entryWalk, recordsWalk } from './app-rx3-walk';
test('RX3 records list → detail → notebook download across three fields', async ({ page }, info) => recordsWalk(page, info));
test('RX3 old addresses keep their hash and invite; legacy escape stays', async ({ page }) => { const entry = await page.request.get('/index.html', { maxRedirects: 0 }); test.skip(entry.status() !== 302, 'Requires the opt-in NEW_UI switch; covered by the RX3 local config and HTTP tests.'); await entryWalk(page); });

test('RX3 Desk controls use body text and large targets', async ({ page }) => deskControls(page));
