import { test, expect } from './routes';
import { APP, mockProtocolScreens } from './app-rx1';

// Lane RX1 (2026-10-25): the confirmation screen and today's values on the
// two tablet projects (portrait 820x1180, landscape 1180x820, touch on). The
// file name matches the Playwright configs' tablet pattern.

test.describe('The new protocol screens on a tablet (lane RX1)', () => {
  test('the cards sit beside the page on a wide tablet and under it on a narrow one; every target is 44 px', async ({ page }) => {
    const server = await mockProtocolScreens(page);
    await page.goto(`${APP}#/upload`);
    await page.getByTestId('file-input').setInputFiles({ name: 'western-blot.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 fake') });
    await page.getByTestId('pages-all').tap();
    await expect(page).toHaveURL(/#\/protocols\/p-rx1-new\/analysis$/);
    expect(server.analysisRequests[0]).not.toContain('first_page');
    await expect(page.getByTestId('analysis-review')).toBeVisible({ timeout: 15_000 });
    await page.getByTestId('analysis-review-link').tap();
    await expect(page.getByTestId('review-progress')).toContainText('확인할 것 0 / 2');
    const viewport = page.viewportSize();
    const image = await page.getByRole('img', { name: '원문 1쪽' }).boundingBox();
    const card = await page.locator('.choice-card').boundingBox();
    if ((viewport?.width ?? 0) >= 900) {
      // Landscape: the card is to the right of the page image.
      expect((card?.x ?? 0)).toBeGreaterThan((image?.x ?? 0) + (image?.width ?? 0) - 1);
    } else {
      // Portrait: the card is below the page image.
      expect((card?.y ?? 0)).toBeGreaterThan((image?.y ?? 0));
    }
    for (const button of await page.locator('.choice-card .btn, .review-nav .btn, .review-line').all()) {
      expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
    await page.locator('.review-line[data-line="4"]').tap();
    await page.getByRole('button', { name: '고른 줄이 맞아요' }).tap();
    await page.getByRole('button', { name: '맞아요' }).tap();
    await expect(page.getByTestId('review-done')).toContainText('확인 끝 — 이제 실험할 수 있어요');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("today's values by touch: one big button for nothing to change", async ({ page }) => {
    const server = await mockProtocolScreens(page);
    await page.goto(`${APP}#/protocols/p-rx1-ready/changes`);
    const none = page.getByTestId('changes-none');
    expect((await none.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    await none.tap();
    await expect(page.getByTestId('changes-chosen')).toContainText('원문대로 진행해요.');
    expect(server.runChanges()).toMatchObject({ changes: [], source: 'wizard' });
  });
});
