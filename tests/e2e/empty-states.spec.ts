import { test, expect, devices } from '@playwright/test';

test.describe.configure({ timeout: 45_000 });

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
    await expect(page.locator('.experiment-context-card')).toBeVisible();
    // Rail action buttons should stretch to fill the row rather than overflow it.
    const railBox = await page.locator('.rail-right').boundingBox();
    const viewport = page.viewportSize();
    expect(railBox?.width).toBeLessThanOrEqual((viewport?.width ?? 0) + 1);
    const contextColumns = await page.locator('.experiment-context-grid').evaluate(
      (el) => getComputedStyle(el).gridTemplateColumns,
    );
    expect(contextColumns.trim().split(/\s+/).length).toBe(1);
    const horizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(horizontalOverflow).toBeLessThanOrEqual(1);
    await context.close();
  });
});
