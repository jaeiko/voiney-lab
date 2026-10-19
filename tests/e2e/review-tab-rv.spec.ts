import { test, expect, type Page, type Route } from './detail-view';

// Lane RV (2026-10-14): the review screen. Server answers are mocked with
// page.route; nothing talks to a model. The screen sends one decision per
// press and reads the server's draft again; it never decides what an item
// holds. No approval: the reviewer's name and "검토 완료하고 등록".
// Lane RV2 stage ② (2026-10-16): the item list became one card at a time, the
// card the server builds for the item; these tests read that card.

const PROTOCOL_ID = 'protocol-gamma-0001';
const target = { kind: 'review_pending', event_id: 'analysis-review-pending-a', recorded_at: '2026-10-14T01:00:00+00:00', revision_id: null, analysis_id: 'a' };

const entry = {
  protocol_id: PROTOCOL_ID, title: 'Protocol Gamma', source_filename: 'gamma.pdf', source_sha256: 'b'.repeat(64),
  revision_id: 'pdf-1', readiness_status: 'analysis_required', analysis_status: 'review_pending', step_count: 0,
  created_at: '2026-10-14T00:50:00+00:00', available_for_execution: false, development_only: false,
  lifecycle_state: 'review_pending', execution_blocker_codes: [],
  review_pending: { needs_review_count: 1, by_reason: { not_in_source: 1 }, failed_pages: [], message_ko: '검토 대기 · 확인 필요 1건(원문에 없음 1)' },
  review_versions: [], current_review_version: null,
};

const review = {
  protocol_id: PROTOCOL_ID, title: 'Protocol Gamma', revision_id: 'pdf-1', source: { filename: 'gamma.pdf', page_count: 3 },
  analysis_available: false, available_for_execution: false, lifecycle_state: 'review_pending', analysis_status: 'review_pending',
  review_pending: entry.review_pending, readiness: { status: 'analysis_required', reasons: [] }, sections: [], constructs: [],
  execution_blockers: [], execution_notices: [], safety_notices: [],
  pipeline: { stage: 'review', stage_ko: '검토', blocked: true, message: '검토 대기 · 확인 필요 1건(원문에 없음 1). 분석은 끝났지만 원문에서 확인하지 못한 항목이 있어 이대로는 실행하지 않습니다.' },
};

const step = (id: string, label: string, text: string, extra: object = {}) => ({
  item_id: id, kind: 'step', kind_ko: '단계', step_id: id.slice(5), step_label: label, section_id: 'prep', text, value_kind_ko: null,
  source: { page: 1, excerpt: text, verified: true, lines: { page: 1, first_line: 3, last_line: 3 }, suggested_lines: null },
  origin: 'source', origin_ko: '원문', needs_review: false, covered_by: null, reasons: [], allowed_actions: ['confirm', 'fix', 'memo', 'reset'],
  decision: null, memo: null, current: text, origin_after: 'source', origin_after_ko: '원문', differs_from_original: false, ...extra,
});

const flagged = (decided = false) => step('step:s2', '2', 'Spin the tube for an hour.', {
  origin: 'ai_suggestion', origin_ko: 'AI 제안', origin_after: 'ai_suggestion', needs_review: true,
  reasons: [{ item_id: 'step:s2', reason: 'not_in_source', reason_ko: '원문에 없음', field: 'evidence', reason_code: 'quote_not_found', page_number: 1, matching_source_pages: [] }],
  source: { page: 1, excerpt: 'Spin the tube for an hour.', verified: false, lines: null, suggested_lines: { page: 1, first_line: 4, last_line: 4, similarity: 0.66 } },
  decision: decided ? { action: 'confirm', source_lines: { page: 1, first_line: 4, last_line: 4, excerpt: '2. Incubate for 10 min at 37 C.' }, value: null, reviewer_name: null, recorded_at: '2026-10-14T01:05:00+00:00' } : null,
});

const card = (decided = false) => ({
  card_id: 'step:s2', item_id: 'step:s2', position: 1, total: 1, progress_ko: '1 / 1', type: 'evidence', kind: 'step', kind_ko: '단계', step_label: '2',
  question_ko: '이 단계의 근거를 원문에서 찾지 못했어요. 원문의 어느 줄인가요?', reasons_ko: ['원문에 없음'], shown: 'Spin the tube for an hour.',
  source: flagged().source,
  choices: [
    { choice_id: 'confirm_lines', label_ko: '고른 줄이 맞아요', request: { item_id: 'step:s2', action: 'confirm' }, needs: 'source_lines', route: null, primary: true },
    { choice_id: 'fix', label_ko: '직접 고칠게요', request: { item_id: 'step:s2', action: 'fix' }, needs: 'source_lines_and_text', route: null, primary: false },
    { choice_id: 'reanalysis', label_ko: '다시 분석', request: null, needs: null, route: 'reanalysis', primary: false },
  ],
  proposal: null, change: null, readings: null, steps: null, memo_allowed: true,
  decided, decision: decided ? { action: 'confirm' } : null, current: 'Spin the tube for an hour.',
});

const draft = (decided = false) => ({
  protocol_id: PROTOCOL_ID, title: 'Protocol Gamma', analysis_status: 'review_pending', available_for_execution: false, target,
  items: [step('step:s1', '1', '1. Add 2 mL of buffer A.'), flagged(decided)], needs_review: ['step:s2'],
  cards: [card(decided)], next_card: decided ? null : 0,
  counts: { items: 2, needs_review: 1, decided: decided ? 1 : 0, undecided: decided ? 0 : 1, changed: 0 },
  pages: [{ page: 1, ocr: false, ocr_unclear: false, unclear_line_count: 0, line_count: 5 }],
  registrable: decided, registration_blockers: decided ? [] : [{ item_id: 'step:s2' }],
});

const page1 = {
  protocol_id: PROTOCOL_ID, page: 1, page_count: 3, ocr: false, ocr_unclear: false, unclear_line_count: 0, render_dpi: null, value_boxes: [],
  lines: ['Protocol Gamma', 'Section preparation', '1. Add 2 mL of buffer A.', '2. Incubate for 10 min at 37 C.', 'Wear gloves.']
    .map((text, index) => ({ number: index + 1, text, has_value: /\d/.test(text), unclear: false })),
};

async function openReview(page: Page) {
  const posted: { url: string; body: any }[] = [];
  let decided = false;
  await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [entry] } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review`, route => route.fulfill({ json: review }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/analysis/status`, route => route.fulfill({ json: { state: 'review_pending', worker_running: false } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review-draft/source-pages/1`, route => route.fulfill({ json: page1 }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review-draft`, route => route.fulfill({ json: draft(decided) }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review-decisions`, async (route: Route) => {
    posted.push({ url: route.request().url(), body: route.request().postDataJSON() });
    decided = true;
    await route.fulfill({ json: { item: flagged(true), counts: draft(true).counts, registrable: true, target } });
  });
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review-versions`, async (route: Route) => {
    posted.push({ url: route.request().url(), body: route.request().postDataJSON() });
    await route.fulfill({ status: 201, json: { protocol: entry, review_version: { version: 1, version_ko: '검토본 v1', revision_id: 'pdf-1-review-1', reviewer_name: '김재준', changes: [] } } });
  });
  await page.goto('/');
  await page.waitForFunction(() => typeof loadReviewDraft === 'function');
  await page.evaluate(id => loadProtocolReview(id), PROTOCOL_ID);
  await expect(page.locator('#protocol-review-editor')).toBeVisible();
  return posted;
}

test.describe('The review screen (lane RV)', () => {
  test('the item waiting for a person comes first and is confirmed with a picked source line', async ({ page }) => {
    const posted = await openReview(page);
    const shown = page.locator('#review-card');
    await expect(page.locator('#review-card-progress')).toHaveText('1 / 1');
    await expect(shown).toContainText('2단계 · 단계');
    await expect(shown).toContainText('원문에 없음');
    await expect(shown.locator('.review-line')).toHaveCount(5);
    await expect(shown.locator('.review-line.is-suggested')).toContainText('2. Incubate for 10 min at 37 C.');
    // Without a picked line the confirm is not sent.
    await shown.getByRole('button', { name: '고른 줄이 맞아요' }).click();
    await expect(shown).toContainText('원문에서 이 항목이 있는 줄을 눌러 골라 주세요.');
    expect(posted).toHaveLength(0);
    await shown.locator('.review-line').nth(3).click();
    await expect(shown).toContainText('고른 줄 · 원문 p.1 4줄');
    await shown.getByRole('button', { name: '고른 줄이 맞아요' }).click();
    await expect.poll(() => posted.length).toBe(1);
    expect(posted[0].body).toEqual({ item_id: 'step:s2', action: 'confirm', target, source_lines: { page: 1, first_line: 4, last_line: 4 } });
    await expect(shown).toContainText('모든 카드를 정했습니다');
  });

  test('registering needs every item decided and the reviewer name', async ({ page }) => {
    const posted = await openReview(page);
    await expect(page.locator('#review-register-block')).toBeHidden();
    await expect(page.locator('#review-register')).toBeDisabled();
    const shown = page.locator('#review-card');
    await shown.locator('.review-line').nth(3).click();
    await shown.getByRole('button', { name: '고른 줄이 맞아요' }).click();
    await expect.poll(() => posted.length).toBe(1);
    await expect(page.locator('#review-register-block')).toBeVisible();
    await expect(page.locator('#review-register')).toBeDisabled();
    await page.locator('#reviewer-name').fill('김재준');
    await expect(page.locator('#review-register')).toBeEnabled();
    await page.locator('#review-register').click();
    await expect(page.locator('#review-register-status')).toContainText('검토본 v1을 등록했습니다 · 검토자 김재준');
    expect(posted[1].body).toEqual({ reviewer_name: '김재준', target });
    for (const word of ['승인', '반려']) await expect(page.locator('#protocol-review-editor')).not.toContainText(word);
  });

  test('a phone shows one column', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openReview(page);
    const shown = page.locator('#review-card');
    const buttons = shown.locator('.choice-card-choices button');
    await expect(buttons).toHaveCount(3);
    const first = await buttons.nth(0).boundingBox();
    const second = await buttons.nth(1).boundingBox();
    expect(first && second).toBeTruthy();
    expect(second!.y).toBeGreaterThan(first!.y + first!.height - 1);
    expect(Math.abs(second!.x - first!.x)).toBeLessThan(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });
});
