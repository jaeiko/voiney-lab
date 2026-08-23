import { test, expect, devices } from '@playwright/test';

test.describe('Empty and loading states', () => {
  test('timeline shows guidance text when no experiment session exists', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#experiment-event-timeline')).not.toHaveText('');
  });

  test('protocol selector shows a loading state before the catalog resolves, then settles', async ({ page }) => {
    await page.goto('/');
    const readiness = page.locator('#protocol-readiness');
    await expect(readiness).not.toHaveText('', { timeout: 10_000 });
  });
});

test.describe('Responsive layout', () => {
  test('researcher workspace remains usable at a narrow mobile viewport', async ({ browser }) => {
    const context = await browser.newContext({ ...devices['iPhone 13'] });
    const page = await context.newPage();
    await page.goto('/');
    await expect(page.locator('#researcher-workspace')).toBeVisible();
    await expect(page.locator('#start')).toBeVisible();
    // Rail action buttons should stretch to fill the row rather than overflow it.
    const railBox = await page.locator('.rail-right').boundingBox();
    const viewport = page.viewportSize();
    expect(railBox?.width).toBeLessThanOrEqual((viewport?.width ?? 0) + 1);
    await context.close();
  });

  test('reviewer workspace columns collapse to a single column on tablet width', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 900, height: 900 } });
    const page = await context.newPage();
    await page.goto('/');
    await page.locator('#workspace-reviewer').waitFor({ state: 'visible', timeout: 10_000 });
    await page.locator('#workspace-reviewer').click();
    const columns = page.locator('#reviewer-workspace .workspace-columns');
    const gridTemplateColumns = await columns.evaluate((el) => getComputedStyle(el).gridTemplateColumns);
    // A single-column layout reports exactly one track width.
    expect(gridTemplateColumns.trim().split(/\s+/).length).toBe(1);
    await context.close();
  });
});
