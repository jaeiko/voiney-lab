import { test, expect } from '@playwright/test';

test.describe('Researcher / Bench workspace', () => {
  test('loads with the researcher workspace active and core state visible', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#researcher-workspace')).toBeVisible();
    await expect(page.locator('#workspace-researcher')).toHaveAttribute('aria-current', 'page');

    // Voice controls
    await expect(page.locator('#start')).toBeVisible();
    await expect(page.locator('#stop')).toBeDisabled();

    // Protocol selection
    await expect(page.locator('#protocol-id')).toBeVisible();

    // Experiment ledger / timeline empty state before any session exists
    await expect(page.locator('#experiment-session-ledger')).toBeVisible();
    await expect(page.locator('#experiment-event-timeline')).toBeVisible();
  });

  test('reviewer and admin nav items become visible for the default dev identity', async ({ page }) => {
    await page.goto('/');
    // loadWorkspaceSession() resolves asynchronously after page load.
    await expect(page.locator('#workspace-reviewer')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('#workspace-admin')).toBeVisible({ timeout: 10_000 });
  });

  test('experiment session status badge is hidden when no experiment is selected', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#experiment-session-status-badge')).toBeHidden();
  });

  test('evidence and observation capture controls are present with non-blank guidance text', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#manual-observation-content')).toBeVisible();
    await expect(page.locator('#experiment-evidence-file')).toBeVisible();
    const captureStatus = page.locator('#experiment-capture-status');
    await expect(captureStatus).not.toHaveText('');
  });
});
