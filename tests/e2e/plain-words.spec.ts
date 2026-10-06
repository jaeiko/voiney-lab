import { test, expect, type Page } from '@playwright/test';

// Lane U (2026-10-05): the researcher's screen in plain words. Every server
// answer here is mocked with page.route or fed to the page's own message
// handler; nothing talks to a model or a speech provider.

const PROTOCOL_ID = 'protocol-ingel-0001';
const REVISION_ID = 'fixture-69517f0fe629d0e4dc35';
const REPORT_ID = 'ER-C9806BA37B70D727BDF8';

const entry = (runnable: boolean) => ({
  protocol_id: PROTOCOL_ID, title: 'In-gel digestion', source_filename: 'in-gel-digestion.pdf',
  source_sha256: 'a'.repeat(64), revision_id: REVISION_ID,
  readiness_status: runnable ? 'guidance_ready' : 'analysis_required',
  approval_status: 'development_only', analysis_status: 'analysis_complete', step_count: 9,
  created_at: '2026-10-05T01:00:00+00:00', available_for_execution: runnable, development_only: true,
  lifecycle_state: runnable ? 'ready' : 'blocked',
  approval: { status: 'development_only', final_approval: false, actor_principal_id: 'local-admin', actor_role: 'lab_admin', recorded_at: '2026-10-05T01:02:00+00:00' },
});

const segments = Array.from({ length: 12 }, (_, i) => ({
  segment_id: `seg-p4-${i}`, source_page_number: 4, segment_index: i,
  excerpt: `Incubate the gel pieces at 37 °C for ${30 + i} min in 25 mM ammonium bicarbonate, then remove the supernatant.`,
}));

const review = {
  protocol_id: PROTOCOL_ID, revision_id: 'pdf-1-analysis-2', analysis_payload_sha256: 'b'.repeat(64),
  source: { filename: 'in-gel-digestion.pdf', sha256: 'a'.repeat(64), page_count: 9 },
  readiness: { status: 'analysis_required', reasons: [] },
  constructs: [{ construct_type: 'SourceAmbiguity', ambiguity_id: 'amb-1', step_id: 'step-4', resolved: false }],
  outstanding_blockers: [{
    code: 'unresolved_ambiguity', kind: 'reviewer_can_clear', step_id: 'step-4', source_page_number: 4,
    reviewer_action: 'resolve_ambiguity', already_acknowledged: false,
    decision_options: ['single_statement_is_authoritative'], clearing_decision: 'single_statement_is_authoritative',
    citable_segments: segments,
  }],
};

// Hash-, revision- and record-id-shaped strings that must not be in the body.
const IDENTIFIER_SHAPES = [/\b[0-9a-f]{16,}\b/i, /\bER-[0-9A-F]{8,}\b/, /\bfixture-[0-9a-f]+/, /pdf-\d+-analysis-\d+/, /\blocal-admin\b/, /SHA-256/];

async function mockCatalog(page: Page, runnable: boolean, findingAnswer?: { status: number; detail: string }) {
  await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [entry(runnable)] } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review`, route => route.fulfill({ json: review }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/revisions/**`, route =>
    route.fulfill({ status: findingAnswer?.status ?? 403, json: { detail: findingAnswer?.detail ?? 'protocol_approval_denied' } }));
}

async function openReview(page: Page, findingAnswer?: { status: number; detail: string }) {
  await mockCatalog(page, false, findingAnswer);
  await page.goto('/');
  await page.waitForFunction(() => typeof renderProtocolReview === 'function');
  await page.locator('#protocol-id').selectOption(PROTOCOL_ID);
  await expect(page.locator('#protocol-blockers .citation-row')).toHaveCount(segments.length);
}

async function acceptSession(page: Page) {
  await page.evaluate(([pid, rid]) => {
    acceptedSessionConfiguration = { configuration_id: 7, server_generation: 0, mode: 'cascade', language: 'ko', protocol_id: pid, revision_id: rid };
  }, [PROTOCOL_ID, REVISION_ID]);
}

test.describe('Plain words on the researcher screen (lane U)', () => {
  test('evidence candidates are one row each and scroll inside a fixed-height list', async ({ page }) => {
    await openReview(page);
    const rows = page.locator('#protocol-blockers .citation-row');
    const geometry = await rows.evaluateAll(nodes => nodes.map(node => {
      const box = node.getBoundingClientRect();
      return { top: box.top, height: box.height, hasBox: !!node.querySelector('input[type=checkbox]'),
        page: node.querySelector('.citation-page')?.textContent, excerpt: node.querySelector('.citation-excerpt')?.textContent };
    }));
    for (let i = 0; i < geometry.length; i += 1) {
      expect(geometry[i].hasBox).toBe(true);
      expect(geometry[i].page).toBe('p.4');
      expect(geometry[i].excerpt).toContain('Incubate the gel pieces');
      // One line per candidate: rows stack, they never sit side by side.
      expect(geometry[i].height).toBeLessThan(48);
      if (i > 0) expect(geometry[i].top).toBeGreaterThanOrEqual(geometry[i - 1].top + geometry[i - 1].height - 1);
    }
    const list = page.locator('#protocol-blockers .citation-list');
    const scroll = await list.evaluate(node => ({ client: node.clientHeight, scroll: node.scrollHeight, overflow: getComputedStyle(node).overflowY }));
    expect(scroll.overflow).toBe('auto');
    expect(scroll.scroll).toBeGreaterThan(scroll.client);
    expect(scroll.client).toBeLessThanOrEqual(220);
    // The segment handle is in the developer details only.
    await expect(list).not.toContainText('seg-p4-');
  });

  test('the body shows readable names, never hash or identifier shapes', async ({ page }) => {
    await mockCatalog(page, true);
    await page.goto('/');
    await page.waitForFunction(() => typeof renderExperimentContext === 'function');
    await page.locator('#protocol-id').selectOption(PROTOCOL_ID);
    await acceptSession(page);
    await page.evaluate(([pid, rid]) => {
      const report = (id: string, started: string) => onMessage({ data: JSON.stringify({
        type: 'experiment.report.state', configuration_id: 7, generation: 0,
        report: { report_id: id, status: 'in_progress', started_at: started, protocol_id: pid, anomaly_count: 0, blocker_count: 0, event_count: 0, events: [] },
      }) }, sessionGeneration, socket);
      void report('ER-C9806BA37B70D727BDF8', '2026-10-05T05:12:00+00:00');
      renderExperimentContext();
    }, [PROTOCOL_ID, REVISION_ID]);
    await page.locator('#procedure-report-details').evaluate(node => { (node as HTMLDetailsElement).open = true; });
    await expect(page.locator('#experiment-context-version')).toContainText('개발용 초안(승인 전)');
    await expect(page.locator('#last-report-id')).toHaveText(/^실험 기록 · In-gel digestion · 2026-10-0\d \d\d:\d\d$/);
    const body = await page.locator('#researcher-workspace').innerText();
    for (const shape of IDENTIFIER_SHAPES) expect(body, `identifier shape ${shape} in the body`).not.toMatch(shape);
    // The original values are kept, folded into the developer details.
    await page.locator('#last-report-dev summary').click();
    await expect(page.locator('#last-report-dev')).toContainText(REPORT_ID);
    await page.locator('#experiment-context-version summary').click();
    await expect(page.locator('#experiment-context-version')).toContainText(REVISION_ID);

    // Two records of the same protocol started the same day are numbered.
    await page.evaluate(([pid]) => onMessage({ data: JSON.stringify({
      type: 'experiment.report.state', configuration_id: 7, generation: 0,
      report: { report_id: 'ER-0000000000000000BEEF', status: 'in_progress', started_at: '2026-10-05T06:40:00+00:00', protocol_id: pid, anomaly_count: 0, blocker_count: 0, event_count: 0, events: [] },
    }) }, sessionGeneration, socket), [PROTOCOL_ID]);
    await expect(page.locator('#last-report-id')).toHaveText(/ · 2번째$/);
  });

  test('the review panel keeps hashes and the revision id out of its body', async ({ page }) => {
    await openReview(page);
    const text = await page.locator('#protocol-review-panel').innerText();
    for (const shape of IDENTIFIER_SHAPES) expect(text, `identifier shape ${shape} in the review panel`).not.toMatch(shape);
    await expect(page.locator('#protocol-review-content')).toContainText('9쪽');
  });

  test('the computational workflow panel is folded away by default', async ({ page }) => {
    await page.goto('/');
    const panel = page.locator('#experiment-workflow-panel');
    await expect(panel).toBeVisible();
    await expect(panel).not.toHaveAttribute('open', '');
    await expect(page.locator('#experiment-workflow-link')).toBeHidden();
    await expect(page.locator('#experiment-workflow-status')).toBeHidden();
    await expect(page.locator('#experiment-session-ledger')).not.toContainText('메타데이터');
    await panel.locator('summary').click();
    await expect(page.locator('#experiment-workflow-link')).toBeVisible();
  });

  test('a turn card shows one status, and a finished turn keeps no progress label', async ({ page }) => {
    await page.goto('/');
    await page.waitForFunction(() => typeof applyTurnState === 'function');
    await acceptSession(page);
    await page.evaluate(() => {
      const send = (turn: number, revision: number, state: string) => onMessage({ data: JSON.stringify({
        type: 'turn.state', configuration_id: 7, turn_id: turn, generation: 0, revision, state }) }, sessionGeneration, socket);
      let r = 0;
      // Turn 1 reaches "playing" and its "complete" never arrives.
      for (const state of ['listening', 'transcribing', 'routing', 'synthesizing', 'playing']) void send(1, ++r, state);
      r = 0;
      for (const state of ['listening', 'transcribing', 'routing', 'cancelled']) void send(2, ++r, state);
      r = 0;
      for (const state of ['listening', 'transcribing']) void send(3, ++r, state);
    });
    const card = (turn: number) => page.locator('#log .turn').filter({ has: page.locator('b', { hasText: new RegExp(`^Turn ${turn}$`, 'i') }) });
    for (const turn of [1, 2, 3]) {
      const statuses = await card(turn).evaluate(node => [...node.querySelectorAll('.turn-status,.error')]
        .filter(item => (item as HTMLElement).offsetParent !== null && item.textContent?.trim())
        .map(item => item.textContent?.trim()));
      expect(statuses.length, `turn ${turn} statuses: ${statuses.join(' | ')}`).toBeLessThanOrEqual(1);
    }
    await expect(card(2).locator('.turn-status')).toHaveText('중단됨');
    await expect(card(2).locator('.error')).toBeHidden();
    await expect(card(1)).not.toContainText('재생 중');
    await expect(card(3).locator('.turn-status')).toHaveText('음성 인식 중…');
  });

  test('a refused finding says why and what to do, on one line by the button', async ({ page }) => {
    await openReview(page);
    const resolve = page.locator('#protocol-blockers button', { hasText: '이 모호성을 해결' });
    const note = page.locator('#protocol-blockers .finding-status');
    // Nothing cited: caught before sending, with the next action.
    let posted = 0;
    page.on('request', request => { if (request.method() === 'POST' && request.url().includes('/findings/')) posted += 1; });
    await resolve.click();
    await expect(note).toHaveText('원문 근거를 하나 이상 고른 뒤 다시 눌러 주세요.');
    expect(posted).toBe(0);
    // The server refuses the content: not a permission problem, and what to do.
    await page.locator('#protocol-blockers .citation-row input').first().check();
    await resolve.click();
    await expect(note).toContainText('기록하지 못했습니다.');
    await expect(note).toContainText('권한 문제는 아닙니다');
    await expect(note).toContainText('다시 눌러 주세요');
    await expect(note).not.toContainText('이 기록을 남길 권한이 없습니다');
    expect(posted).toBe(1);
  });

  test('a real permission refusal names the reviewer role and the next step', async ({ page }) => {
    await openReview(page, { status: 403, detail: 'authorization_denied' });
    await page.locator('#protocol-blockers .citation-row input').first().check();
    await page.locator('#protocol-blockers button', { hasText: '이 모호성을 해결' }).click();
    const note = page.locator('#protocol-blockers .finding-status');
    await expect(note).not.toContainText('검토자 역할이 있는 계정만');
    await expect(note).toContainText('검토자 계정으로 로그인한 뒤 다시 눌러 주세요');
  });

  test('while paused the resume button is large and in the step card', async ({ page }) => {
    await mockCatalog(page, true);
    await page.goto('/');
    await page.waitForFunction(() => typeof renderCuratedProtocolState === 'function');
    await acceptSession(page);
    const state = (status: string, revision: number) => ({
      attached: true, protocol_id: PROTOCOL_ID, revision_id: REVISION_ID, display_name: 'In-gel digestion', development_only: true,
      readiness_status: 'guidance_ready', active: true, current_step_label: '4', current_step_id: 'step-4', total_steps: 9,
      at_final_step: false, block_reason: null, revision, display_summary: '4 Incubate at 37 °C.', primary_summary: '4단계: 37 °C 에서 둡니다.',
      source_language: 'en', spoken_summary: '4단계입니다.', warning_texts: [], warning_presentations: [], visual_assets: [],
      visual_status: 'unavailable', source_page_refs: [4], workflow_status: status,
    });
    const send = (payload: object) => page.evaluate(message => onMessage({ data: JSON.stringify(message) }, sessionGeneration, socket), payload);
    await page.evaluate(() => { sessionActive = true; });
    await send({ type: 'protocol.fixture.state', configuration_id: 7, action: 'pause', state: state('paused', 5) });
    const resume = page.locator('#workflow-resume-button');
    await expect(page.locator('#workflow-paused-banner')).toBeVisible();
    await expect(resume).toBeVisible();
    await expect(resume).toBeEnabled();
    const size = await resume.boundingBox();
    expect(size?.height ?? 0).toBeGreaterThanOrEqual(40);
    await expect(page.locator('#rail-pause-session')).toHaveText('▶ 다시 시작');
    // Resumed by the server: the banner goes and the rail says pause again.
    await send({ type: 'protocol.fixture.state', configuration_id: 7, action: 'resume', state: state('active', 6) });
    await expect(page.locator('#workflow-paused-banner')).toBeHidden();
    await expect(page.locator('#rail-pause-session')).toHaveText('일시정지');
    // An ended experiment refuses both, and the screen says why.
    await send({ type: 'workflow.control.refused', configuration_id: 7, action: 'pause', reason: 'experiment_ended', status: 'stopped' });
    await expect(page.locator('#status')).toContainText('실험이 이미 끝나 일시정지·다시 시작을 할 수 없습니다');
    await expect(page.locator('#rail-pause-session')).toBeDisabled();
  });

  test('the microphone asks for echo cancellation and says so when it is off', async ({ page }) => {
    await page.goto('/');
    await page.waitForFunction(() => typeof ensureAudio === 'function');
    const result = await page.evaluate(async () => {
      let requested: MediaStreamConstraints | null = null;
      const track = { getSettings: () => ({ echoCancellation: false, noiseSuppression: true, autoGainControl: true }), stop() {} };
      const fake = { getAudioTracks: () => [track], getTracks: () => [track] };
      navigator.mediaDevices.getUserMedia = async (constraints?: MediaStreamConstraints) => { requested = constraints ?? null; return fake as unknown as MediaStream; };
      try { await ensureAudio(sessionGeneration); } catch (_) { /* the fake stream cannot feed a real AudioContext */ }
      return requested;
    });
    const audio = (result as { audio: Record<string, boolean> }).audio;
    expect(audio.echoCancellation).toBe(true);
    expect(audio.noiseSuppression).toBe(true);
    expect(audio.autoGainControl).toBe(true);
    await expect(page.locator('#audio-echo-notice')).toBeVisible();
    await expect(page.locator('#audio-echo-notice')).toContainText('헤드폰');
  });
});
