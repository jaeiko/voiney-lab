import { test, expect } from '@playwright/test';

async function openAdminWorkspace(page) {
  await page.goto('/');
  await page.locator('#workspace-admin').waitFor({ state: 'visible', timeout: 10_000 });
  await page.locator('#workspace-admin').click();
  await expect(page.locator('#admin-workspace')).toBeVisible();
}

test.describe('Lab Admin workspace', () => {
  test('shows connector, membership, and retention sections', async ({ page }) => {
    await openAdminWorkspace(page);
    await expect(page.locator('#admin-connectors')).toBeVisible();
    await expect(page.locator('#admin-memberships')).toBeVisible();
    await expect(page.locator('#admin-metrics')).toBeVisible();
    await expect(page.locator('#admin-retention-days')).toBeVisible();
  });

  test('does not claim credentials are visible or providers are live-tested', async ({ page }) => {
    await openAdminWorkspace(page);
    const body = await page.locator('#admin-workspace').innerText();
    expect(body).not.toContain('실시간 테스트 완료');
    expect(body).toContain('secret://');
  });

  test('dev identity vs OIDC operational auth boundary is stated', async ({ page }) => {
    await openAdminWorkspace(page);
    await expect(page.locator('#admin-workspace')).toContainText('운영 범위는 OIDC가 필수입니다');
  });
});
