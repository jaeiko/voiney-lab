import { test, expect } from '@playwright/test';

async function openReviewerWorkspace(page) {
  await page.goto('/');
  await page.locator('#workspace-reviewer').waitFor({ state: 'visible', timeout: 10_000 });
  await page.locator('#workspace-reviewer').click();
  await expect(page.locator('#reviewer-workspace')).toBeVisible();
}

test.describe('Reviewer workspace', () => {
  test('shows the inbox, diff, and decision panels', async ({ page }) => {
    await openReviewerWorkspace(page);
    await expect(page.locator('#reviewer-inbox')).toBeVisible();
    await expect(page.locator('#reviewer-diff')).toBeVisible();
    await expect(page.locator('#reviewer-approve')).toBeVisible();
    await expect(page.locator('#reviewer-reject')).toBeVisible();
    await expect(page.locator('#reviewer-revoke')).toBeVisible();
  });

  test('the approve action is visually distinct from OCR acceptance so the two can never be confused', async ({ page }) => {
    await openReviewerWorkspace(page);
    const approveClass = await page.locator('#reviewer-approve').getAttribute('class');
    expect(approveClass).toContain('btn-reviewer-approve');
    // The researcher-side OCR acceptance control lives in a different workspace
    // entirely (own protocol upload review), and carries its own neutral class.
    await page.locator('#workspace-researcher').click();
    const ocrAcceptClass = await page.locator('#protocol-ocr-accept').getAttribute('class');
    expect(ocrAcceptClass).toContain('btn-ocr-neutral');
    expect(ocrAcceptClass).not.toContain('btn-reviewer-approve');
  });

  test('source connector panels (protocols.io, Drive, GitHub) are present and read-only-labelled', async ({ page }) => {
    await openReviewerWorkspace(page);
    await expect(page.locator('#protocols-io-import')).toBeVisible();
    await expect(page.locator('#drive-sync')).toBeVisible();
    await expect(page.locator('#github-import')).toBeVisible();
    await expect(page.locator('#github-status')).toContainText('실행하지 않습니다');
  });
});
