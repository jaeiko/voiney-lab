import { test, expect, type Page, type Route, TAB } from './routes';

// Lane IA decision ③ (2026-10-19): the upload as one screen at a time --
// the file, the analysis as it runs, the result. The server's answers are
// mocked with page.route (the CI server has no analysis model); the page's
// own upload path, analysis call and polling run as they are.

const PROTOCOL_ID = 'p-upload-1';
const entry = (status: 'running' | 'ready' | 'review') => ({
  protocol_id: PROTOCOL_ID, title: 'Theta (예시)', revision_id: 'pdf-1', source_filename: 'theta.pdf', source_sha256: 'd'.repeat(64),
  available_for_execution: status === 'ready', analysis_status: status === 'ready' ? 'review_required' : status === 'review' ? 'review_pending' : 'structured_analysis_ready',
  lifecycle_state: status === 'ready' ? 'ready' : status === 'review' ? 'review_pending' : 'uploaded', readiness_status: status === 'ready' ? 'guidance_ready' : 'analysis_required',
  step_count: 4, created_at: '2026-10-19T02:00:00+00:00', development_only: false, execution_blocker_codes: [], analysis_outdated_codes: [], analysis_outdated_ko: null,
  review_versions: [], current_review_version: null, ...(status === 'review' ? { review_pending: { needs_review_count: 2, by_reason: {}, failed_pages: [], message_ko: '확인 필요 2건' } } : {}),
  analysis_run: { state: status === 'ready' ? 'review_required' : status === 'review' ? 'review_pending' : 'analyzing', failure_code: null, reanalysis: false, requested_at: new Date(Date.now() - 65000).toISOString() },
});
const review = (status: 'ready' | 'review') => ({
  protocol_id: PROTOCOL_ID, title: 'Theta (예시)', revision_id: 'pdf-1', lifecycle_state: status === 'ready' ? 'ready' : 'review_pending', readiness_status: 'guidance_ready',
  available_for_execution: status === 'ready', analysis_available: true, source: { filename: 'theta.pdf', sha256: 'd'.repeat(64), page_count: 2 },
  readiness: { status: 'guidance_ready', label: '안내 준비 완료', reasons: [] }, analysis_failure: null, constructs: [], execution_blockers: [], execution_notices: [],
  step_count: 4, sections: [], timers: [], safety_notices: [], safety_scope: { usage_scope: 'source_protocol_only', source_protocol_only: true, notice: '기관 안전 문서 없음 · 원문 안전 주의만' },
  pipeline: { stage: 'ready', stage_ko: '실행 가능', blocked: false, message: '분석 통과' },
  ...(status === 'review' ? { review_pending: { needs_review_count: 2 } } : {}),
});

async function mockUpload(page: Page, ends: 'ready' | 'review') {
  let polls = 0;
  let done = false;
  await page.route('**/api/protocols?filename=*', (route: Route) => route.fulfill({ status: 201, json: { protocol: entry('running'), deduplicated: false } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/analysis/status`, (route: Route) => {
    polls += 1;
    if (polls >= 2) done = true;
    route.fulfill({ json: done
      ? { state: ends === 'ready' ? 'review_required' : 'review_pending', worker_running: false, requested_at: entry('running').analysis_run.requested_at, pipeline: { message: ends === 'ready' ? '분석 통과' : '확인 필요 2건' } }
      : { state: 'analyzing', worker_running: true, requested_at: entry('running').analysis_run.requested_at } });
  });
  await page.route(`**/api/protocols/${PROTOCOL_ID}/analysis`, (route: Route) => route.fulfill({ status: 202, json: { analysis_request_accepted: true, analysis_run: { state: 'analyzing' } } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review`, (route: Route) => route.fulfill({ json: review(ends) }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/reagent-tables`, (route: Route) => route.fulfill({ json: { tables: [] } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/start-check`, (route: Route) => route.fulfill({ json: { enabled: true, questions: [], skipped_reason: 'no_numeric_values' } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review-draft`, (route: Route) => route.fulfill({ json: { protocol_id: PROTOCOL_ID, title: 'Theta (예시)', target: { kind: 'review_pending' }, items: [], cards: [], next_card: null, counts: {}, pages: [], registrable: false, registration_blockers: [] } }));
  await page.route('**/api/protocols', (route: Route) => route.fulfill({ json: { protocols: done ? [entry(ends)] : [] } }));
}

test.describe('Upload, one screen at a time (lane IA)', () => {
  test('a public document link uses the same analysis flow at tablet width', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await mockUpload(page, 'ready');
    const url = 'https://docs.google.com/document/d/synthetic-public/view';
    let imports = 0;
    await page.route('**/api/protocols/import-link', async (route: Route) => {
      expect(route.request().method()).toBe('POST');
      expect(route.request().postDataJSON()).toEqual({ url });
      imports += 1;
      await route.fulfill({ status: 201, json: { protocol: entry('running'), deduplicated: false } });
    });
    await page.goto(TAB.upload);
    await page.getByLabel('공개 링크로 가져오기').fill(url);
    await page.getByRole('button', { name: '링크로 가져오기', exact: true }).click();
    await expect(page.locator('#upload-step-progress')).toBeVisible();
    await expect(page.locator('#upload-result-title')).toHaveText('바로 쓸 수 있어요', { timeout: 10_000 });
    expect(imports).toBe(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    await expect(page.locator('#wizard-sheet')).toBeHidden();
  });

  test('a private link shows the server refusal and allows correction', async ({ page }) => {
    const message = '이 파일은 비공개라 가져올 수 없어요. 파일을 내려받아 올려 주세요.';
    await page.route('**/api/protocols/import-link', (route: Route) => route.fulfill({
      status: 403, json: { detail: { code: 'link_private', message } },
    }));
    await page.goto(TAB.upload);
    const field = page.getByLabel('공개 링크로 가져오기');
    const url = 'https://drive.google.com/file/d/synthetic-private/view';
    await field.fill(url);
    await page.getByRole('button', { name: '링크로 가져오기', exact: true }).click();
    await expect(page.locator('#protocol-upload-status')).toHaveText(message);
    await expect(page.locator('#upload-step-pick')).toBeVisible();
    await expect(field).toHaveValue(url);
    await expect(field).toBeEnabled();
  });

  test('the file, the analysis, then "바로 쓸 수 있어요" and the start', async ({ page }) => {
    await mockUpload(page, 'ready');
    await page.goto(TAB.upload);
    await expect(page.locator('#upload-step-pick')).toBeVisible();
    await expect(page.locator('#upload-step-progress')).toBeHidden();
    await page.locator('#protocol-pdf').setInputFiles({ name: 'theta.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 stand-in') });
    await expect(page.locator('#upload-step-progress')).toBeVisible();
    await expect(page.locator('#upload-progress-title')).toHaveText('분석하고 있어요');
    await expect(page.locator('#upload-step-result')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('#upload-result-title')).toHaveText('바로 쓸 수 있어요');
    await expect(page.locator('#upload-result-actions')).toContainText('이 프로토콜로 실험 시작');
    await page.locator('#upload-result-actions button', { hasText: '이 프로토콜로 실험 시작' }).click();
    await expect(page.locator('#wizard-sheet')).toBeVisible();
    await expect(page.locator('#wizard-title')).toHaveText('안전 주의');
  });

  test('an analysis that waits for a person leads to the protocol\'s cards', async ({ page }) => {
    await mockUpload(page, 'review');
    await page.goto(TAB.upload);
    await page.locator('#protocol-pdf').setInputFiles({ name: 'theta.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 stand-in') });
    await expect(page.locator('#upload-step-result')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('#upload-result-title')).toHaveText('확인할 것 2개가 있어요');
    await page.locator('#upload-result-actions a', { hasText: '확인 시작' }).click();
    await expect(page).toHaveURL(new RegExp(`#/protocols/${PROTOCOL_ID}$`));
    await expect(page.locator('#protocol-detail-view')).toBeVisible();
    await expect(page.locator('#protocol-detail-badges')).toContainText('확인할 것 2개');
  });
});
