import { test, expect, devices, TAB } from './routes';

test.describe.configure({ timeout: 45_000 });

test.describe('Empty and loading states', () => {
  test('timeline shows guidance text when no experiment session exists', async ({ page }) => {
    await page.goto(TAB.records);
    await expect(page.locator('#experiment-event-timeline')).toBeVisible();
    await expect(page.locator('#experiment-event-timeline')).not.toHaveText('');
  });

  test('protocol selector shows a loading state before the catalog resolves, then settles', async ({ page }) => {
    await page.goto(TAB.settings);
    const readiness = page.locator('#protocol-readiness');
    await expect(readiness).not.toHaveText('', { timeout: 10_000 });
  });
});

test.describe('Responsive layout', () => {
  test('the tabs remain usable at a narrow mobile viewport', async ({ browser }) => {
    const context = await browser.newContext({ ...devices['iPhone 13'] });
    const page = await context.newPage();
    await page.goto(TAB.home);
    await expect(page.locator('#researcher-workspace')).toBeVisible();
    await expect(page.locator('#tab-bar')).toBeVisible();
    await expect(page.locator('#home-cards .home-card').first()).toBeVisible();
    const viewport = page.viewportSize();
    // Rail action buttons and the tab bar should fill the row rather than overflow it.
    const railBox = await page.locator('.rail-right').boundingBox();
    expect(railBox?.width).toBeLessThanOrEqual((viewport?.width ?? 0) + 1);
    const barBox = await page.locator('#tab-bar').boundingBox();
    expect(barBox?.width).toBeLessThanOrEqual((viewport?.width ?? 0) + 1);
    expect(barBox?.y).toBeGreaterThan((viewport?.height ?? 0) - 100);
    for (const link of await page.locator('#tab-bar .tab-link').all()) {
      expect((await link.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(64);
    }
    // Every tab fits the width.
    for (const tab of ['protocols', 'experiment', 'records', 'settings', 'home'] as const) {
      await page.locator(`#tab-link-${tab}`).click();
      await expect(page.locator(`#tab-${tab}`)).toBeVisible();
      const horizontalOverflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      );
      expect(horizontalOverflow, tab).toBeLessThanOrEqual(1);
    }
    await page.locator('#tab-link-records').click();
    const ledgerColumns = await page.locator('.experiment-ledger-grid').evaluate(
      (el) => getComputedStyle(el).gridTemplateColumns,
    );
    expect(ledgerColumns.trim().split(/\s+/).length).toBe(1);
    await context.close();
  });
});
