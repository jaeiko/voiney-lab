import { test, expect, type Page, TAB } from './routes';

// Lane IA decision ① (2026-10-19): five tabs by hash route, a bar below, the
// back button; while the experiment runs the run's tab is the default, the bar
// hides and another tab carries the band "실험 중 · n단계 · 돌아가기". The CI
// server's catalog is empty, so the home shows a first user's one card; a run
// is driven through the page's own message handler with a mocked catalog.

const PROTOCOL_ID = 'protocol-ia-0001';
const REVISION_ID = 'pdf-1-analysis-1';
const entry = {
  protocol_id: PROTOCOL_ID, title: 'In-gel digestion', source_filename: 'in-gel-digestion.pdf', source_sha256: 'a'.repeat(64),
  revision_id: REVISION_ID, readiness_status: 'guidance_ready', analysis_status: 'review_required', step_count: 9,
  created_at: '2026-10-19T01:00:00+00:00', available_for_execution: true, development_only: false, lifecycle_state: 'ready',
  execution_blocker_codes: [],
};
const REVIEW = {
  protocol_id: PROTOCOL_ID, title: 'In-gel digestion', revision_id: REVISION_ID, lifecycle_state: 'ready', readiness_status: 'guidance_ready',
  available_for_execution: true, analysis_available: true, source: { filename: 'in-gel-digestion.pdf', sha256: 'a'.repeat(64), page_count: 9 },
  readiness: { status: 'guidance_ready', label: '안내 준비 완료', reasons: [] }, analysis_failure: null, constructs: [],
  execution_blockers: [], execution_notices: [], step_count: 9, sections: [], timers: [],
  safety_notices: [{ step_label: '2', step_id: 's2', source_page_number: 1, source_text: 'Wear gloves.', primary_text: '장갑을 끼세요.' }],
  safety_scope: { usage_scope: 'source_protocol_only', source_protocol_only: true, notice: '기관 안전 문서 없음 · 원문 안전 주의만' },
  pipeline: { stage: 'ready', stage_ko: '실행 가능', blocked: false, message: '분석 통과' },
};
const state = {
  attached: true, protocol_id: PROTOCOL_ID, revision_id: REVISION_ID, display_name: 'In-gel digestion', development_only: false,
  readiness_status: 'guidance_ready', active: true, current_step_label: '4', current_step_id: 'step-4', total_steps: 9,
  at_final_step: false, block_reason: null, revision: 1, display_summary: '4 Incubate at 37 °C.', primary_summary: '4단계: 37 °C 에서 둡니다.',
  source_language: 'en', spoken_summary: '4단계입니다.', warning_texts: [], warning_presentations: [], visual_assets: [],
  visual_status: 'unavailable', source_page_refs: [4], workflow_status: 'running',
};

async function startRun(page: Page) {
  await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [entry] } }));
  await page.goto(TAB.records);
  await page.waitForFunction(() => typeof renderCuratedProtocolState === 'function');
  await page.evaluate(([pid, rid, value]) => {
    acceptedSessionConfiguration = { configuration_id: 7, server_generation: 0, mode: 'cascade', language: 'ko', protocol_id: pid, revision_id: rid };
    sessionActive = true;
    onMessage({ data: JSON.stringify({ type: 'protocol.fixture.state', configuration_id: 7, action: 'start', state: value }) }, sessionGeneration, socket);
  }, [PROTOCOL_ID, REVISION_ID, state] as const);
  await expect(page.locator('body')).toHaveAttribute('data-view', 'during');
}

test.describe('Five tabs (lane IA)', () => {
  test('the home opens first with its cards, each link shows its tab, and back returns', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#tab-home')).toBeVisible();
    await expect(page.locator('#tab-bar')).toBeVisible();
    await expect(page.locator('#tab-link-home')).toHaveAttribute('aria-current', 'page');
    // The CI catalog is empty: a first user sees the one card.
    await expect(page.locator('#home-cards .home-card')).toHaveCount(1);
    await expect(page.locator('#home-cards .home-card')).toContainText('먼저 프로토콜을 올려 주세요');
    await expect(page.locator('#tab-protocols')).toBeHidden();

    await page.locator('#tab-link-protocols').click();
    await expect(page).toHaveURL(/#\/protocols$/);
    await expect(page.locator('#tab-protocols')).toBeVisible();
    await expect(page.locator('#protocol-library')).toBeVisible();
    await expect(page.locator('#tab-home')).toBeHidden();

    await page.locator('#tab-link-settings').click();
    await expect(page).toHaveURL(/#\/settings$/);
    await expect(page.locator('#settings-sheet')).toBeVisible();
    await expect(page.locator('#install-guide')).toBeVisible();
    await expect(page.locator('#settings-account')).toContainText('개발 모드');

    await page.goBack();
    await expect(page).toHaveURL(/#\/protocols$/);
    await expect(page.locator('#tab-protocols')).toBeVisible();
    await expect(page.locator('#tab-settings')).toBeHidden();

    await page.locator('#tab-link-experiment').click();
    await expect(page.locator('#experiment-start-open')).toBeVisible();
    await expect(page.locator('#start-wizard')).toBeHidden();
    await page.locator('#tab-link-records').click();
    await expect(page.locator('#experiment-session-ledger')).toBeVisible();
  });

  test('the step-by-step screens open as a sheet over the page and close by ×, outside, Esc and back', async ({ page }) => {
    await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [entry] } }));
    await page.goto(TAB.home);
    const sheet = page.locator('#wizard-sheet');
    await page.locator('#home-cards .home-card[data-card="new-experiment"]').click();
    await expect(sheet).toBeVisible();
    await expect(page.locator('#start-wizard')).toBeVisible();
    await expect(page.locator('#wizard-title')).toHaveText('마이크 확인 · 목소리 맞추기');
    await expect(page.locator('#tab-home')).toBeVisible();
    await expect(page).toHaveURL(/#\/home$/);
    expect((await page.locator('#wizard-primary').boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(64);
    await page.locator('#wizard-close').click();
    await expect(sheet).toBeHidden();
    await expect(page.locator('#tab-home')).toBeVisible();

    await page.locator('#home-cards .home-card[data-card="new-experiment"]').click();
    await expect(sheet).toBeVisible();
    await page.locator('#wizard-sheet-backdrop').click({ position: { x: 5, y: 5 } });
    await expect(sheet).toBeHidden();

    await page.locator('#home-cards .home-card[data-card="new-experiment"]').click();
    await expect(sheet).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(sheet).toBeHidden();

    await page.locator('#home-cards .home-card[data-card="new-experiment"]').click();
    await expect(sheet).toBeVisible();
    await page.goBack();
    await expect(sheet).toBeHidden();
    await expect(page).toHaveURL(/#\/home$/);
    await expect(page.locator('#tab-home')).toBeVisible();
  });

  test('the protocol\'s start opens the sheet from the questions with that protocol picked', async ({ page }) => {
    await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [entry] } }));
    await page.route(`**/api/protocols/${PROTOCOL_ID}/review`, route => route.fulfill({ json: REVIEW }));
    await page.route(`**/api/protocols/${PROTOCOL_ID}/start-check`, route => route.fulfill({ json: { enabled: true, questions: [], skipped_reason: 'no_numeric_values' } }));
    await page.goto(`/#/protocols/${PROTOCOL_ID}`);
    await expect(page.locator('#protocol-start')).toBeVisible();
    await page.locator('#protocol-start').click();
    await expect(page.locator('#wizard-sheet')).toBeVisible();
    // No numeric values to ask about: the sheet moves on to the safety check.
    await expect(page.locator('#wizard-title')).toHaveText('안전 주의');
    await expect(page.locator('#protocol-id')).toHaveValue(PROTOCOL_ID);
    await expect(page.locator('#tab-protocols')).toBeVisible();
  });

  test('the first card opens the upload box on the protocols tab', async ({ page }) => {
    await page.goto('/');
    await page.locator('#home-cards .home-card[data-card="first"]').click();
    await expect(page).toHaveURL(/#\/upload$/);
    await expect(page.locator('#tab-upload')).toBeVisible();
    await expect(page.locator('#upload-step-pick')).toBeVisible();
    await expect(page.locator('#protocol-pdf-pick')).toBeVisible();
    await expect(page.locator('#protocol-pdf-pick')).toBeFocused();
    await expect(page.locator('#tab-link-protocols')).toHaveAttribute('aria-current', 'page');
  });

  test('a run opens the experiment tab, hides the bar, and another tab carries the band', async ({ page }) => {
    await startRun(page);
    await expect(page).toHaveURL(/#\/experiment$/);
    await expect(page.locator('#procedure-primary')).toContainText('37 °C 에서 둡니다.');
    await expect(page.locator('#tab-bar')).toBeHidden();
    await expect(page.locator('#run-return-band')).toBeHidden();
    // The records stay under the run view, as lane UI0 left them.
    await expect(page.locator('#experiment-session-ledger')).toBeVisible();

    await page.evaluate(() => navigateTab('records'));
    await expect(page).toHaveURL(/#\/records$/);
    await expect(page.locator('#run-return-band')).toBeVisible();
    await expect(page.locator('#run-return-text')).toHaveText('실험 중 · 4단계');
    await expect(page.locator('#rail-step-badge')).toHaveText('4단계 / 9');
    await expect(page.locator('#rail-timer-badge')).toBeVisible();
    await expect(page.locator('#rail-safety-badge')).toBeVisible();
    await expect(page.locator('#procedure-primary')).toBeHidden();

    await page.locator('#run-return-link').click();
    await expect(page).toHaveURL(/#\/experiment$/);
    await expect(page.locator('#run-return-band')).toBeHidden();
    await expect(page.locator('#procedure-primary')).toBeVisible();
    // The back button leaves the run's tab for the one before the run (the records), never a tab that was replaced.
    await page.goBack();
    await expect(page).toHaveURL(/#\/records$/);
    await expect(page.locator('#run-return-band')).toBeVisible();
  });

  test('a wide screen shows the bar as a menu on the left', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 860 });
    await page.goto(TAB.protocols);
    const bar = await page.locator('#tab-bar').boundingBox();
    const main = await page.locator('#tab-protocols').boundingBox();
    expect(bar?.x).toBe(0);
    expect(bar?.height ?? 0).toBeGreaterThan(400);
    expect(main?.x ?? 0).toBeGreaterThanOrEqual((bar?.width ?? 0) - 1);
  });
});
