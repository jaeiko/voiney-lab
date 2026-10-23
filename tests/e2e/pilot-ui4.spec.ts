import { test, expect, type Page, TAB } from './routes';

// Lane UI4 (2026-10-23) in the browser, on a phone and a desktop: the screen items needed before
// the pilot (lane QA's severity-3 items and the hand-overs of lanes IA and OWN). Server answers
// are mocked with page.route; the run's state is driven through the page's own message handler.

const PROTOCOL_ID = 'protocol-ui4-0001';
const REVISION_ID = 'pdf-1-analysis-1';
const ENTRY = {
  protocol_id: PROTOCOL_ID, title: 'In-gel digestion', source_filename: 'in-gel-digestion.pdf', source_sha256: 'a'.repeat(64),
  revision_id: REVISION_ID, readiness_status: 'guidance_ready', analysis_status: 'structured_analysis_ready', step_count: 9,
  created_at: '2026-10-19T01:00:00+00:00', available_for_execution: true, development_only: false, lifecycle_state: 'ready',
  execution_blocker_codes: [],
};
const PENDING = {
  ...ENTRY, protocol_id: 'protocol-ui4-0002', title: 'Western blot (검토 대기)', available_for_execution: false,
  analysis_status: 'review_pending', lifecycle_state: 'review_pending', readiness_status: 'analysis_required',
  review_pending: { needs_review_count: 2, by_reason: {}, failed_pages: [], message_ko: '검토 대기 · 확인 필요 2건' },
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

async function mock(page: Page, experiments: object[] = []) {
  await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [ENTRY, PENDING] } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review`, route => route.fulfill({ json: REVIEW }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/reagent-tables`, route => route.fulfill({ json: { tables: [] } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/start-check`, route => route.fulfill({ json: { enabled: true, questions: [], skipped_reason: 'no_numeric_values' } }));
  await page.route('**/api/workspace/experiments', route => route.fulfill({ json: { experiments } }));
  await page.route('**/api/workspace/experiments/*/timeline', route => route.fulfill({ json: { session: null, timeline: [], observation_count: 0, evidence_count: 0 } }));
}

async function acceptRun(page: Page, state: object) {
  await page.waitForFunction(() => typeof renderCuratedProtocolState === 'function' && protocolCatalog.size > 0);
  await page.evaluate(([pid, rid, value]) => {
    acceptedSessionConfiguration = { configuration_id: 7, server_generation: 0, mode: 'cascade', language: 'ko', protocol_id: pid, revision_id: rid };
    sessionActive = true;
    onMessage({ data: JSON.stringify({ type: 'protocol.fixture.state', configuration_id: 7, action: 'start', state: value }) }, sessionGeneration, socket);
  }, [PROTOCOL_ID, REVISION_ID, state] as const);
}

test.describe('Screen items before the pilot (lane UI4)', () => {
  test('QA-18: during the run the home leads back to it; after it the protocols tab shows its list', async ({ page }) => {
    await mock(page);
    await page.goto(TAB.experiment);
    await acceptRun(page, STATE);
    await expect(page.locator('body')).toHaveAttribute('data-view', 'during');
    await page.evaluate(() => navigateTab('home'));
    await expect(page.locator('#home-cards .home-card')).toHaveCount(1);
    await expect(page.locator('#home-cards .home-card[data-card="running"]')).toBeVisible();
    // The experiment ends by voice: the session is still on, so the upload waits.
    await page.evaluate(() => {
      renderState('LISTENING');
      onMessage({ data: JSON.stringify({ type: 'experiment.ended', configuration_id: 7, status: 'completed', report: 'saved' }) }, sessionGeneration, socket);
    });
    await expect(page.locator('body')).toHaveAttribute('data-view', 'after');
    await page.evaluate(() => navigateTab('home'));
    await expect(page.locator('#home-cards .home-card[data-card="upload"]')).toHaveCount(0);
    await expect(page.locator('#home-cards')).toContainText('음성 연결이 끝나면 새 프로토콜을 올릴 수 있어요.');
    await page.locator('#home-cards .home-card[data-card="review"]').click();
    await expect(page).toHaveURL(/#\/protocols$/);
    await expect(page.locator('#protocol-list')).toBeVisible();
    await expect(page.locator('#protocol-list')).toContainText('Western blot');
  });

  test('QA-19: the run\'s [설정] shows the settings sheet over the run, and only it', async ({ page }) => {
    await mock(page);
    await page.goto(TAB.experiment);
    await acceptRun(page, STATE);
    await expect(page.locator('body')).toHaveAttribute('data-view', 'during');
    await expect(page.locator('#settings-sheet')).toBeHidden();
    await page.locator('#run-open-settings').click();
    await expect(page.locator('body')).toHaveAttribute('data-sheet', 'settings');
    await expect(page.locator('#settings-sheet')).toBeVisible();
    await expect(page.locator('#mic-device')).toBeVisible();
    await expect(page.locator('#settings-account')).toBeHidden();
    await expect(page.locator('#settings-agent-voice')).toBeHidden();
    await page.locator('#run-close-settings').click();
    await expect(page.locator('#settings-sheet')).toBeHidden();
    await expect(page.locator('#tab-settings')).toBeHidden();
  });

  test('the account is deleted from the settings after one more ask, and the login comes back', async ({ page }) => {
    // The CI server runs in dev mode; the account session and the deletion are stand-ins.
    let signedIn = true;
    const account = { account_id: 'a-ui4', name: '김재준', admin: false, entitlement: 'pilot', organization_id: 'lab-pilot', disabled: false };
    await mock(page);
    await page.route('**/api/auth/session', route => route.fulfill({ json: signedIn ? { mode: 'accounts', signed_in: true, account } : { mode: 'accounts', signed_in: false, account: null } }));
    const sent: object[] = [];
    await page.route('**/api/auth/delete-account', async route => {
      sent.push(route.request().postDataJSON());
      signedIn = false;
      await route.fulfill({ json: { deleted: true, message_ko: '계정과 그 계정이 남긴 기록을 지웠어요.', removed: {} } });
    });
    await page.goto(TAB.settings);
    await expect(page.locator('#account-delete-open')).toBeVisible();
    await expect(page.locator('#account-delete-form')).toBeHidden();
    await page.locator('#account-delete-open').click();
    await expect(page.locator('#account-delete-warning')).toContainText('되돌릴 수 없어요');
    await page.locator('#account-delete-password').fill('correct horse');
    await page.locator('#account-delete-send').click();
    await expect(page.locator('body')).toHaveAttribute('data-auth', 'required');
    await expect(page.locator('#wizard-name')).toBeVisible();
    expect(sent).toEqual([{ password: 'correct horse' }]);
  });

  test('item 5: a record picked in the records tab links to its reports as the end screen does', async ({ page }) => {
    const experiment = { session_id: 's-ui4-1', protocol_id: PROTOCOL_ID, protocol_title: 'In-gel digestion', status: 'stopped', current_step_label: '5', started_at: '2026-10-20T01:00:00+00:00' };
    const report = { report_id: 'ER-0000000000000000UI41', session_id: 's-ui4-1', protocol_id: PROTOCOL_ID, protocol_title: 'In-gel digestion', status: 'completed',
      started_at: '2026-10-20T01:00:00+00:00', ended_at: '2026-10-20T02:00:00+00:00', anomaly_count: 0, blocker_count: 0, finalization_version: 1, development_only: false };
    await mock(page, [experiment]);
    await page.route('**/api/workspace/experiments/s-ui4-1/reports', route => route.fulfill({ json: { session_id: 's-ui4-1', reports: [report] } }));
    await page.goto(TAB.records);
    await expect(page.locator('#records-detail-reports')).toBeHidden();
    await page.locator('#records-list .records-row').first().click();
    await expect(page).toHaveURL(/#\/records\/s-ui4-1$/);
    const row = page.locator('#records-report-list .records-report');
    await expect(row).toHaveCount(1);
    await expect(row).toContainText('실험 기록 · In-gel digestion');
    const links = row.locator('.report-actions a');
    await expect(links).toHaveText(['보고서 만들기 (Word)', 'Markdown 내보내기', 'JSON 내보내기', 'CSV 내보내기']);
    await expect(links.first()).toHaveAttribute('href', `/api/experiment-reports/${report.report_id}.docx`);
    await expect(links.first()).toBeVisible();
  });
});
