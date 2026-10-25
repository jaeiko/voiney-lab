import { test, expect } from './routes';
import { APP, ENTRY_ANALYSING, ENTRY_READY, ENTRY_REVIEW, mockCatalog } from './app-rx0';

// Lane RX0 (2026-10-24): the new screen on the two tablet projects (portrait
// 820x1180, landscape 1180x820, touch on). The file name matches the
// Playwright configs' tablet pattern, so only those projects run it.

test.describe('The new screen on a tablet (lane RX0)', () => {
  test('the home and the list fit the width with 44 px targets and a wide-screen side menu', async ({ page }) => {
    await mockCatalog(page, [ENTRY_READY, ENTRY_REVIEW, ENTRY_ANALYSING]);
    await page.goto(APP);
    await expect(page.locator('.big-card')).toContainText('확인하기');
    const viewport = page.viewportSize();
    const nav = page.getByRole('navigation', { name: '화면 고르기' });
    const navBox = await nav.boundingBox();
    if ((viewport?.width ?? 0) >= 900) {
      // Landscape: the menu is a column on the left.
      expect(navBox?.x).toBe(0);
      expect(navBox?.width ?? 0).toBeLessThan(300);
      expect(navBox?.height ?? 0).toBeGreaterThan(400);
    } else {
      // Portrait: the menu is the bar along the bottom.
      expect(navBox?.y).toBeGreaterThan((viewport?.height ?? 0) - 100);
    }
    for (const link of await nav.getByRole('link').all()) {
      expect((await link.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
    await nav.getByText('프로토콜', { exact: true }).tap();
    await expect(page.locator('.row')).toHaveCount(3);
    for (const link of await page.locator('.row .btn').all()) {
      expect((await link.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
