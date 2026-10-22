import { test, expect, type Page, TAB } from './routes';

// Lane IA stage ③ (2026-10-21) in the browser, on a phone and a desktop: the end screen keeps
// to its tab and its console start opens the sheet (lane QA's QA-01), the questions the server
// asks before the start show on the run view (QA-12), and the records tab opens on a list of
// past experiments, one tap to its record (decision ⑤). Server answers are mocked with
// page.route; the run's state is driven through the page's own message handler.

const PROTOCOL_ID = 'protocol-ia3-0001';
const REVISION_ID = 'pdf-1-analysis-1';
const ENTRY = {
  protocol_id: PROTOCOL_ID, title: 'In-gel digestion', source_filename: 'in-gel-digestion.pdf', source_sha256: 'a'.repeat(64),
  revision_id: REVISION_ID, readiness_status: 'guidance_ready', analysis_status: 'review_required', step_count: 9,
  created_at: '2026-10-19T01:00:00+00:00', available_for_execution: true, development_only: false, lifecycle_state: 'ready',
  execution_blocker_codes: [],
};
const REVIEW = {
  protocol_id: PROTOCOL_ID, title: 'In-gel digestion', revision_id: REVISION_ID, lifecycle_state: 'ready', readiness_status: 'guidance_ready',
  available_for_execution: true, analysis_available: true, source: { filename: 'in-gel-digestion.pdf', sha256: 'a'.repeat(64), page_count: 9 },
  readiness: { status: 'guidance_ready', label: '안내 준비 완료', reasons: [] }, analysis_failure: null, constructs: [],
  execution_blockers: [], execution_notices: [], step_count: 9, sections: [], timers: [], safety_notices: [],
  pipeline: { stage: 'ready', stage_ko: '실행 가능', blocked: false, message: '분석 통과' },
};
const STATE = {
  attached: true, protocol_id: PROTOCOL_ID, revision_id: REVISION_ID, display_name: 'In-gel digestion', development_only: false,
  readiness_status: 'guidance_ready', active: true, current_step_label: '4', current_step_id: 'step-4', total_steps: 9,
  at_final_step: false, block_reason: null, revision: 1, display_summary: '4 Incubate at 37 °C.', primary_summary: '4단계: 37 °C 에서 둡니다.',
  source_language: 'en', spoken_summary: '4단계입니다.', warning_texts: [], warning_presentations: [], visual_assets: [],
  visual_status: 'unavailable', source_page_refs: [4], workflow_status: 'running',
};
const EXPERIMENTS = [
  { session_id: 's-ia3-1', protocol_id: PROTOCOL_ID, protocol_title: 'In-gel digestion', status: 'paused', current_step_label: '3', started_at: '2026-10-20T01:00:00+00:00' },
  { session_id: 's-ia3-2', protocol_id: PROTOCOL_ID, protocol_title: 'In-gel digestion', status: 'stopped', current_step_label: '5', started_at: '2026-10-19T01:00:00+00:00' },
];

async function mock(page: Page, experiments: object[] = []) {
  await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [ENTRY] } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review`, route => route.fulfill({ json: REVIEW }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/reagent-tables`, route => route.fulfill({ json: { tables: [] } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/start-check`, route => route.fulfill({ json: { enabled: true, questions: [], skipped_reason: 'no_numeric_values' } }));
  await page.route('**/api/workspace/experiments', route => route.fulfill({ json: { experiments } }));
  await page.route('**/api/workspace/experiments/*/timeline', route => route.fulfill({ json: { session: null, timeline: [], observation_count: 0, evidence_count: 0 } }));
}

async function acceptRun(page: Page, state: object, screen: object | null = null) {
  await page.waitForFunction(() => typeof renderCuratedProtocolState === 'function' && protocolCatalog.size > 0);
  await page.evaluate(([pid, rid, value, fields]) => {
    acceptedSessionConfiguration = { configuration_id: 7, server_generation: 0, mode: 'cascade', language: 'ko', protocol_id: pid, revision_id: rid };
    sessionActive = true;
    onMessage({ data: JSON.stringify({ type: 'protocol.fixture.state', configuration_id: 7, action: 'start', state: value, ...(fields ? { screen: fields } : {}) }) }, sessionGeneration, socket);
  }, [PROTOCOL_ID, REVISION_ID, state, screen] as const);
}

test.describe('Stage ③ of the screen structure (lane IA)', () => {
  test('the end screen keeps to the experiment tab and its start opens the sheet', async ({ page }) => {
    await mock(page);
    await page.goto(TAB.experiment);
    await page.waitForFunction(() => typeof pickProtocolFromList === 'function' && protocolCatalog.size > 0);
    await page.evaluate(pid => pickProtocolFromList(pid), PROTOCOL_ID);
    await acceptRun(page, STATE);
    await expect(page.locator('body')).toHaveAttribute('data-view', 'during');
    await page.evaluate(() => onMessage({ data: JSON.stringify({ type: 'experiment.ended', configuration_id: 7, status: 'completed', report: 'saved' }) }, sessionGeneration, socket));
    await expect(page.locator('body')).toHaveAttribute('data-view', 'after');
    await page.evaluate(() => navigateTab('home'));
    await expect(page.locator('#tab-home')).toBeVisible();
    await expect(page.locator('#tab-experiment')).toBeHidden();
    await expect(page.locator('#start')).toBeHidden();
    await page.evaluate(() => navigateTab('experiment'));
    await page.evaluate(() => stopSession());
    await expect(page.locator('#start')).toBeEnabled();
    await page.locator('#start').click();
    await expect(page.locator('#start-wizard')).toBeVisible();
    await expect(page.locator('body')).toHaveAttribute('data-wizard', 'on');
    await expect(page.locator('body')).toHaveAttribute('data-view', 'after');
    expect(await page.evaluate(() => sessionActive)).toBe(false);
  });

  test('the question before the start shows on the run view while it is asked', async ({ page }) => {
    await mock(page);
    await page.goto(TAB.experiment);
    await acceptRun(page, { ...STATE, active: false, workflow_status: 'preview' }, { open_question: { kind: 'prestart', text: '7단계 조건에 해당하나요? 네 · 아니요 · 나중에' } });
    await expect(page.locator('body')).toHaveAttribute('data-view', 'during');
    await expect(page.locator('#procedure-open-question')).toBeVisible();
    await expect(page.locator('#procedure-open-question')).toContainText('7단계 조건에 해당하나요?');
    await expect(page.locator('#experiment-start')).toBeHidden();
  });

  test('the records open on the list of past experiments, one tap to its record and back', async ({ page }) => {
    await mock(page, EXPERIMENTS);
    await page.goto(TAB.records);
    const rows = page.locator('#records-list .records-row');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText('In-gel digestion');
    await expect(rows.nth(0)).toContainText('일시정지');
    await expect(rows.nth(1)).toContainText('종료됨');
    await expect(page.locator('#experiment-session-ledger')).toBeHidden();
    await rows.nth(0).click();
    await expect(page).toHaveURL(/#\/records\/s-ia3-1$/);
    await expect(page.locator('#records-detail-view')).toBeVisible();
    await expect(page.locator('#records-detail-line')).toContainText('3단계');
    await expect(page.locator('#experiment-session-ledger')).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/#\/records$/);
    await expect(page.locator('#records-list-view')).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });
});
