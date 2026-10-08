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
  analysis_status: 'review_required', step_count: 9,
  created_at: '2026-10-05T01:00:00+00:00', available_for_execution: runnable, development_only: true,
  lifecycle_state: runnable ? 'ready' : 'blocked',
  execution_blocker_codes: runnable ? [] : ['no_executable_steps'],
});

// Lane DI (2026-10-08): the review carries the execution rule's two lists and
// the source's safety statements; there is no reviewer finding to make.
const review = {
  protocol_id: PROTOCOL_ID, revision_id: 'pdf-1-analysis-2', analysis_payload_sha256: 'b'.repeat(64), analysis_available: true,
  source: { filename: 'in-gel-digestion.pdf', sha256: 'a'.repeat(64), page_count: 9 },
  readiness: { status: 'analysis_required', reasons: [] },
  constructs: [{ construct_type: 'SourceAmbiguity', ambiguity_id: 'amb-1', step_id: 'step-4', resolved: false }],
  execution_blockers: [{ code: 'no_executable_steps', kind: 'blocking', message_ko: '실행할 단계를 원문에서 찾지 못했습니다.', source_page_number: null, source_excerpt: null, step_id: null }],
  execution_notices: [{ code: 'unresolved_ambiguity', kind: 'source_note', step_id: 'step-4', source_page_number: 4,
    message_ko: '원문에 서로 다른 두 서술이 있습니다.', source_excerpt: 'Incubate the gel pieces at 37 °C for 30 min.' }],
  safety_notices: [{ step_label: '4', step_id: 'step-4', source_page_number: 4, source_text: 'Wear gloves.', primary_text: '장갑을 착용하세요.' }],
};

// Hash-, revision- and record-id-shaped strings that must not be in the body.
const IDENTIFIER_SHAPES = [/\b[0-9a-f]{16,}\b/i, /\bER-[0-9A-F]{8,}\b/, /\bfixture-[0-9a-f]+/, /pdf-\d+-analysis-\d+/, /\blocal-admin\b/, /SHA-256/];

async function mockCatalog(page: Page, runnable: boolean) {
  await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [entry(runnable)] } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review`, route => route.fulfill({ json: review }));
}

async function openReview(page: Page) {
  await mockCatalog(page, false);
  await page.goto('/');
  await page.waitForFunction(() => typeof renderProtocolReview === 'function');
  await page.locator('#protocol-id').selectOption(PROTOCOL_ID);
  await expect(page.locator('#protocol-start-summary')).toBeVisible();
}

async function acceptSession(page: Page) {
  await page.evaluate(([pid, rid]) => {
    acceptedSessionConfiguration = { configuration_id: 7, server_generation: 0, mode: 'cascade', language: 'ko', protocol_id: pid, revision_id: rid };
  }, [PROTOCOL_ID, REVISION_ID]);
}

test.describe('Plain words on the researcher screen (lane U)', () => {
  test('the start screen shows the safety statements beside their Korean and the blocker', async ({ page }) => {
    await openReview(page);
    const summary = page.locator('#protocol-start-summary');
    await expect(summary.locator('.safety-notice-source')).toHaveText('Wear gloves.');
    await expect(summary.locator('.safety-notice-korean')).toHaveText('장갑을 착용하세요.');
    await expect(summary).toContainText('실행을 막는 사유 1건');
    await expect(summary).toContainText('시작 전 알림 1건');
    await expect(page.locator('#protocol-start')).toBeHidden();
    // Nothing here is folded: the statements are visible without a click.
    await expect(summary.locator('details')).toHaveCount(0);
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
    await expect(page.locator('#experiment-context-version')).toContainText('개발용 큐레이션 분석');
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
