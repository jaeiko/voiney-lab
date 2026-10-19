import { test, expect, type Page, type Route } from './detail-view';

// Lane RV2 stage ② (human decision 2026-10-16): review is one card at a time,
// chosen by buttons. Server answers are mocked with page.route; nothing talks
// to a model. A press sends the button's own request and the answer's
// next_card moves the screen on; "← 이전" goes back. The card draws only the
// fields the server gives it -- here a page of OCR values with their own
// buttons and the pieces of the image they were read from.

const PROTOCOL_ID = 'protocol-cards-0001';
const target = { kind: 'review_pending', event_id: 'analysis-review-pending-c', recorded_at: '2026-10-16T01:00:00+00:00', revision_id: null, analysis_id: 'c' };
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII=', 'base64');

const entry = {
  protocol_id: PROTOCOL_ID, title: 'Cell wash', source_filename: 'cell-wash-scan.pdf', source_sha256: 'c'.repeat(64),
  revision_id: 'pdf-1', readiness_status: 'analysis_required', analysis_status: 'review_pending', step_count: 0,
  created_at: '2026-10-16T00:50:00+00:00', available_for_execution: false, development_only: false,
  lifecycle_state: 'review_pending', execution_blocker_codes: [],
  review_pending: { needs_review_count: 2, by_reason: { ocr_value: 2 }, failed_pages: [], message_ko: '검토 대기 · 확인 필요 2건(OCR 값 확인 2)' },
  review_versions: [], current_review_version: null,
};
const review = {
  protocol_id: PROTOCOL_ID, title: 'Cell wash', revision_id: 'pdf-1', source: { filename: 'cell-wash-scan.pdf', page_count: 3 },
  analysis_available: false, available_for_execution: false, lifecycle_state: 'review_pending', analysis_status: 'review_pending',
  review_pending: entry.review_pending, readiness: { status: 'analysis_required', reasons: [] }, sections: [], constructs: [],
  execution_blockers: [], execution_notices: [], safety_notices: [],
  pipeline: { stage: 'review', stage_ko: '검토', blocked: true, message: '검토 대기 · 확인 필요 2건(OCR 값 확인 2).' },
};
const choice = (id: string, label: string, request: object | null, extra: object = {}) =>
  ({ choice_id: id, label_ko: label, request, needs: null, route: null, primary: false, ...extra });
const valueCard = (decided: boolean) => ({
  card_id: 'ocr_value:2:4000rpm', item_id: 'ocr_value:2:4000rpm', position: 1, total: 2, progress_ko: '1 / 2', type: 'ocr_value',
  kind: 'ocr_value', kind_ko: 'OCR 값', step_label: null, question_ko: '원문 그림에서 이 값이 4000 rpm 맞나요?',
  reasons_ko: ['OCR 엔진 하나만 읽은 쪽'], shown: '4000 rpm', source: { page: 2, verified: true, value_boxes: [0] },
  choices: [choice('confirm', '4000 rpm 맞아요', { item_id: 'ocr_value:2:4000rpm', action: 'confirm' }, { primary: true }),
    choice('fix', '직접 입력', { item_id: 'ocr_value:2:4000rpm', action: 'fix' }, { needs: 'text' })],
  proposal: null, change: null, readings: null, steps: null, memo_allowed: true,
  decided, decision: decided ? { action: 'confirm' } : null, current: '4000 rpm',
});
const pageCard = (decided: boolean) => ({
  card_id: 'ocr_page:3', item_id: 'ocr_page:3', position: 2, total: 2, progress_ko: '2 / 2', type: 'ocr_page', kind: 'ocr_page',
  kind_ko: 'OCR 값', page: 3, step_label: null, question_ko: '원문 3쪽 그림의 값 1개를 확인해 주세요.', reasons_ko: [], shown: null,
  source: { page: 3, value_boxes: [1] },
  values: [{ item_id: 'ocr_value:3:5min', shown: '5 min', reason_ko: '두 OCR 엔진이 이 값을 다르게 읽음',
    readings: [{ engine: 'google', text: '5 min', token: '5min' }, { engine: 'clova', text: '6 min', token: '6min' }], value_boxes: [1],
    choices: [choice('reading_0', '5 min', { item_id: 'ocr_value:3:5min', action: 'confirm' }, { primary: true }),
      choice('reading_1', '6 min', { item_id: 'ocr_value:3:5min', action: 'fix', text: '6 min' }),
      choice('fix', '직접 입력', { item_id: 'ocr_value:3:5min', action: 'fix' }, { needs: 'text' })],
    decided, decision: decided ? { action: 'fix' } : null, current: '5 min' }],
  choices: [choice('confirm_all', '모두 맞아요', { item_id: 'ocr_page:3', action: 'confirm' }, { primary: true })],
  proposal: null, change: null, readings: null, steps: null, memo_allowed: false, decided, decision: null, current: null,
});
const cards = (decided: number) => [valueCard(decided >= 1), pageCard(decided >= 2)];
const draft = (decided: number) => ({
  protocol_id: PROTOCOL_ID, title: 'Cell wash', analysis_status: 'review_pending', available_for_execution: false, target, items: [],
  cards: cards(decided), next_card: decided >= 2 ? null : decided, needs_review: [],
  counts: { items: 4, needs_review: 2, decided, undecided: 2 - decided, changed: 0 }, pages: [], registrable: decided >= 2, registration_blockers: [],
});

async function openCards(page: Page) {
  const posted: any[] = [];
  let decided = 0;
  await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [entry] } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review`, route => route.fulfill({ json: review }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/analysis/status`, route => route.fulfill({ json: { state: 'review_pending', worker_running: false } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review-draft`, route => route.fulfill({ json: draft(decided) }));
  await page.route('**/value-boxes/*/image', route => route.fulfill({ contentType: 'image/png', body: PNG }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review-decisions`, async (route: Route) => {
    posted.push(route.request().postDataJSON());
    decided = Math.min(2, decided + 1);
    const after = draft(decided);
    await route.fulfill({ json: { item: {}, counts: after.counts, registrable: after.registrable, target, cards: after.cards, next_card: after.next_card } });
  });
  await page.goto('/');
  await page.waitForFunction(() => typeof loadReviewDraft === 'function');
  await page.evaluate(id => loadProtocolReview(id), PROTOCOL_ID);
  await expect(page.locator('#review-card')).toBeVisible();
  return posted;
}

test.describe('The review cards (lane RV2)', () => {
  test('a press moves to the next card and ← 이전 goes back; buttons are a hand tall', async ({ page }) => {
    const posted = await openCards(page);
    const shown = page.locator('#review-card');
    await expect(page.locator('#review-card-progress')).toHaveText('1 / 2');
    await expect(shown.locator('.choice-card-value')).toHaveText('4000 rpm');
    for (const button of await shown.locator('.choice-card-choices button').all()) {
      expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(64);
    }
    await shown.getByRole('button', { name: '4000 rpm 맞아요' }).click();
    await expect(page.locator('#review-card-progress')).toHaveText('2 / 2');
    expect(posted[0]).toEqual({ item_id: 'ocr_value:2:4000rpm', action: 'confirm', target });
    await page.locator('#review-card-prev').click();
    await expect(page.locator('#review-card-progress')).toHaveText('1 / 2');
    await expect(shown).toContainText('확인함');
    await expect(page.locator('#review-card-next')).toBeVisible();
  });

  test('a page of OCR values shows each value with its buttons and its piece of the image', async ({ page }) => {
    const posted = await openCards(page);
    const shown = page.locator('#review-card');
    await shown.getByRole('button', { name: '4000 rpm 맞아요' }).click();
    await expect(page.locator('#review-card-progress')).toHaveText('2 / 2');
    const row = shown.locator('.choice-card-values-row');
    await expect(row).toHaveCount(1);
    await expect(row).toContainText('Google · 5 min  /  CLOVA · 6 min');
    await expect(row.locator('img')).toHaveAttribute('src', /\/source-pages\/3\/value-boxes\/1\/image$/);
    await row.getByRole('button', { name: '6 min' }).click();
    await expect.poll(() => posted.length).toBe(2);
    expect(posted[1]).toEqual({ item_id: 'ocr_value:3:5min', action: 'fix', text: '6 min', target });
    await expect(shown).toContainText('모든 카드를 정했습니다');
    await expect(page.locator('#review-register-block')).toBeVisible();
  });

  test('keys: a button pressed with Enter, ← back to the card before', async ({ page }) => {
    const posted = await openCards(page);
    await page.locator('#review-card').getByRole('button', { name: '4000 rpm 맞아요' }).focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#review-card-progress')).toHaveText('2 / 2');
    expect(posted).toHaveLength(1);
    await page.locator('#review-card').getByRole('button', { name: '모두 맞아요' }).focus();
    await page.keyboard.press('ArrowLeft');
    await expect(page.locator('#review-card-progress')).toHaveText('1 / 2');
  });
});
