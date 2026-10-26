import { test, expect } from './routes';
import { APP, mockProtocolScreens } from './app-rx1';

// Lane RX1 (2026-10-25): the new screen's protocol pages on the desktop and
// the phone (the tablet walk is tablet-app-rx1.spec.ts). Every server answer
// is mocked (app-rx1.ts): the walk is contract-tested against the same
// shapes the server routes return, not live-tested.

test.describe('The new screen: upload → pages → analysis → confirmation → start (lane RX1)', () => {
  test('a file is uploaded, the pages are asked with small pictures, the analysis progress shows, every card is confirmed beside its page, and 실험 시작 appears', async ({ page }) => {
    const server = await mockProtocolScreens(page);
    await page.goto(`${APP}#/upload`);
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('프로토콜 올리기');
    await expect(page.getByText('원문을 올리면 값과 안전 주의를 그 원문에서만 읽어 안내해요.')).toBeVisible();
    // The protocols menu item is the current one for these pages.
    await expect(page.locator('[data-nav="protocols"]')).toHaveAttribute('aria-current', 'page');

    await page.getByTestId('file-input').setInputFiles({ name: 'western-blot.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 fake') });
    await expect(page.getByText('어느 쪽을 분석할까요?')).toBeVisible();
    await expect(page.getByTestId('pages-all')).toContainText('전체 분석');
    await page.getByTestId('pages-pick').click();
    const thumbs = page.locator('.page-thumb');
    await expect(thumbs).toHaveCount(3);
    await expect(page.getByTestId('page-picker-hint')).toHaveText('시작 쪽을 눌러 주세요.');
    for (const thumb of await thumbs.all()) {
      expect(await thumb.locator('img').evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    }
    await thumbs.nth(1).click();
    await expect(page.getByTestId('page-picker-hint')).toHaveText('끝 쪽을 눌러 주세요. 한 쪽만이면 같은 쪽을 다시 눌러요.');
    await thumbs.nth(2).click();
    await expect(page.getByTestId('page-picker-hint')).toHaveText('2–3쪽을 분석해요');
    await page.getByTestId('pages-start').click();

    // The analysis progress: the clock, the pieces, the measured estimate, the leave line; the cover-line title gives way to the file's name.
    await expect(page).toHaveURL(/#\/protocols\/p-rx1-new\/analysis$/);
    expect(server.analysisRequests[0]).toContain('first_page=2&last_page=3');
    await expect(page.getByTestId('protocol-title')).toHaveText('western-blot');
    await expect(page.getByTestId('title-read-as')).toContainText('표지 최종 하리');
    await expect(page.getByTestId('analysis-running')).toContainText('지난 시간 01:');
    await expect(page.getByTestId('analysis-pieces')).toHaveText('나눠 읽는 중 1/2');
    await expect(page.getByTestId('analysis-estimate')).toContainText('지금까지 속도로 어림');
    await expect(page.getByTestId('analysis-running')).toContainText('기다리는 동안 다른 화면을 봐도 돼요.');

    // The name can be changed from here.
    await page.getByTestId('title-rename').click();
    await page.getByLabel('프로토콜 이름').fill('웨스턴 블롯');
    await page.getByRole('button', { name: '저장하기' }).click();
    await expect(page.getByTestId('protocol-title')).toHaveText('웨스턴 블롯');

    // The run ends with cards (the screen re-reads every 5 s).
    await expect(page.getByTestId('analysis-review')).toContainText('확인할 것 2개가 있어요', { timeout: 15_000 });
    await page.getByTestId('analysis-review-link').click();

    // The confirmation screen: the page on the left with a band, one card on the right, the progress.
    await expect(page).toHaveURL(/#\/protocols\/p-rx1-new\/review$/);
    await expect(page.getByTestId('review-progress')).toContainText('확인할 것 0 / 2');
    await expect(page.getByRole('img', { name: '원문 1쪽' })).toBeVisible();
    await expect(page.locator('.page-band[data-kind="card"]')).toHaveCount(1);
    await expect(page.locator('.page-image')).not.toHaveAttribute('data-estimated', 'true');
    // The source is never listed whole: only the lines of the page a button needs picked.
    await expect(page.locator('.review-line')).toHaveCount(5);
    await page.locator('.review-line[data-line="4"]').click();
    await expect(page.locator('.page-band[data-kind="picked"]')).toHaveCount(1);
    await page.getByRole('button', { name: '고른 줄이 맞아요' }).click();
    await expect(page.getByTestId('review-progress')).toContainText('확인할 것 1 / 2');
    await expect(page.getByRole('img', { name: '원문 2쪽' })).toBeVisible();
    await page.getByRole('button', { name: '맞아요' }).click();
    await expect(page.getByTestId('review-done')).toContainText('확인 끝 — 이제 실험할 수 있어요');
    expect(server.isRegistered()).toBe(true);
    expect(server.decisions).toHaveLength(2);
    expect(server.decisions[0]).toMatchObject({ item_id: 'step:s2', action: 'confirm', source_lines: { page: 1, first_line: 4, last_line: 4 } });
    await expect(page.getByTestId('review-start')).toHaveText('실험 시작');
    await expect(page.getByTestId('review-start')).toHaveAttribute('href', '/index.html#/protocols/p-rx1-new');
    // No internal word anywhere on these screens.
    for (const word of ['검토 대기', '검토본', '리비전', '카탈로그', 'OCR', '실행 불가']) await expect(page.getByText(word)).toHaveCount(0);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("today's values: two values changed with number and unit, the comparison, and the choice sent once", async ({ page }) => {
    const server = await mockProtocolScreens(page);
    await page.goto(`${APP}#/protocols/p-rx1-ready/changes`);
    await expect(page.getByText('오늘 다르게 할 값을 미리 적어 두면 안내와 타이머가 그 값으로 가요.')).toBeVisible();
    await expect(page.getByTestId('changes-none')).toContainText('바꿀 값 없음 — 원문대로 할게요');
    await page.getByTestId('changes-some').click();
    await expect(page.locator('.row')).toHaveCount(3);
    await page.locator('.row[data-step="s2"]').getByRole('button', { name: '2단계' }).click();
    await expect(page.getByTestId('step-korean')).toHaveText('2. 37 °C에서 10분 동안 둡니다.');
    await expect(page.getByTestId('step-source')).toHaveCount(0);
    await page.getByTestId('step-source-toggle').click();
    await page.getByTestId('step-source').getByRole('button', { name: '10 min' }).click();
    await page.getByTestId('value-number').fill('20');
    await expect(page.getByTestId('value-compare')).toHaveText('원문: 10분 → 오늘: 20분');
    await page.getByTestId('value-use').click();
    await page.getByTestId('step-values').getByRole('button', { name: '온도 37°C' }).click();
    await page.getByTestId('value-number').fill('40');
    await expect(page.getByTestId('value-compare')).toHaveText('원문: 37°C → 오늘: 40°C');
    await page.getByTestId('value-use').click();
    await expect(page.getByText('고른 값 2개')).toBeVisible();
    await page.getByTestId('changes-done').click();
    await expect(page.getByTestId('changes-chosen')).toContainText('오늘 바꾼 값 2개로 진행해요.');
    expect(server.runChanges()).toEqual({ target: { kind: 'analysis', revision_id: 'pdf-1-analysis-1', analysis_id: 'analysis-1' }, source: 'wizard', changes: [{ item_id: 's2#1', value: '20분' }, { item_id: 's2#2', value: '40°C' }] });
    await expect(page.getByTestId('changes-start')).toHaveText('실험 시작');
    for (const button of await page.locator('.btn').all()) {
      expect((await button.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
  });
});
