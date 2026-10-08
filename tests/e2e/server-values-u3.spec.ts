import { test, expect, type Page } from '@playwright/test';

// Lane U3 (2026-10-06): the screen reads the values lane R6 added for it.
// Server answers are mocked with page.route or fed to the page's own message
// handler; nothing talks to a model or a speech provider. The screen shows
// the server's values, never changes what the server decided.

// The server keeps times in UTC; the screen shows the researcher's local
// time. A fixed time zone makes the expected local text exact.
test.use({ timezoneId: 'Asia/Seoul' });

const PROTOCOL_ID = 'protocol-ingel-0001';
const REVISION_ID = 'fixture-69517f0fe629d0e4dc35';

const entry = (runnable = false) => ({
  protocol_id: PROTOCOL_ID, title: 'In-gel digestion', source_filename: 'in-gel-digestion.pdf',
  source_sha256: 'a'.repeat(64), revision_id: REVISION_ID,
  readiness_status: runnable ? 'guidance_ready' : 'analysis_required',
  analysis_status: 'review_required', step_count: 25,
  created_at: '2026-10-05T01:00:00+00:00', available_for_execution: runnable, development_only: !runnable,
  lifecycle_state: runnable ? 'ready' : 'blocked',
});

const review = {
  protocol_id: PROTOCOL_ID, revision_id: 'pdf-1-analysis-2', source: { filename: 'in-gel-digestion.pdf', page_count: 9 }, analysis_available: true,
  readiness: { status: 'analysis_required', reasons: [] },
  constructs: [{ construct_type: 'SourceAmbiguity', ambiguity_id: 'amb-1', step_id: 'step-4', resolved: false }],
  execution_blockers: [], execution_notices: [{ code: 'unresolved_ambiguity', kind: 'source_note', step_id: 'step-4', source_page_number: 4, message_ko: '원문에 서로 다른 두 서술이 있습니다.' }],
  safety_notices: [],
};

async function openPage(page: Page, protocol = entry()) {
  await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [protocol] } }));
  await page.route(`**/api/protocols/${PROTOCOL_ID}/review`, route => route.fulfill({ json: review }));
  await page.goto('/');
  await page.waitForFunction(() => typeof renderExperimentContext === 'function');
  await page.evaluate(([pid, rid]) => {
    acceptedSessionConfiguration = { configuration_id: 7, server_generation: 0, mode: 'cascade', language: 'ko', protocol_id: pid, revision_id: rid };
    sessionActive = true;
  }, [PROTOCOL_ID, REVISION_ID]);
}

const send = (page: Page, message: object) => page.evaluate(m => onMessage({ data: JSON.stringify(m) }, sessionGeneration, socket), message);

const record = (id: string, started: string, sequence: number, day: string) => ({
  report_id: id, status: 'in_progress', started_at: started, protocol_id: PROTOCOL_ID,
  protocol_title: 'In-gel digestion (서버 제목)', day_sequence: sequence, day_sequence_date: day, timezone: 'UTC',
  anomaly_count: 0, blocker_count: 0,
});

test.describe('Server values on the screen (lane U3)', () => {
  test('a record is named by the server title and the server count, in local time', async ({ page }) => {
    await openPage(page);
    const first = record('ER-0000000000000000AAAA', '2026-10-06T01:05:00+00:00', 1, '2026-10-06');
    const second = record('ER-0000000000000000BBBB', '2026-10-06T05:12:00+00:00', 2, '2026-10-06');
    await send(page, { type: 'experiment.report.state', configuration_id: 7, generation: 0,
      report: { ...second, event_count: 0, events: [], reports: [first, second] } });
    // 05:12 UTC is 14:12 in Seoul. The browser has seen one record only, so
    // the number is the server's, not a count of what this page saw.
    await expect(page.locator('#last-report-id')).toHaveText('실험 기록 · In-gel digestion (서버 제목) · 2026-10-06 14:12 · 2번째');
    const rows = page.locator('#experiment-report-list .report-list-row b');
    await expect(rows.nth(0)).toContainText('실험 기록 · In-gel digestion (서버 제목) · 2026-10-06 10:05 · 1번째');
    await expect(rows.nth(1)).toContainText('2026-10-06 14:12 · 2번째');
    // The server's UTC day is kept in the developer details.
    await expect(page.locator('#last-report-dev')).toContainText('2026-10-06 (UTC)');
  });

  test('the first record of a day alone carries no number', async ({ page }) => {
    await openPage(page);
    const only = record('ER-0000000000000000CCCC', '2026-10-06T02:00:00+00:00', 1, '2026-10-06');
    await send(page, { type: 'experiment.report.state', configuration_id: 7, generation: 0,
      report: { ...only, event_count: 0, events: [], reports: [only] } });
    await expect(page.locator('#last-report-id')).toHaveText('실험 기록 · In-gel digestion (서버 제목) · 2026-10-06 11:00');
  });

  test('when the server UTC day is not the local day, its count is not shown as a local count', async ({ page }) => {
    await openPage(page);
    // 23:30 UTC on the 5th is 08:30 on the 6th in Seoul.
    const early = record('ER-0000000000000000DDDD', '2026-10-05T23:30:00+00:00', 3, '2026-10-05');
    await send(page, { type: 'experiment.report.state', configuration_id: 7, generation: 0,
      report: { ...early, event_count: 0, events: [], reports: [early] } });
    await expect(page.locator('#last-report-id')).toHaveText('실험 기록 · In-gel digestion (서버 제목) · 2026-10-06 08:30');
  });

  test('without server values the browser count is kept', async ({ page }) => {
    await openPage(page);
    for (const [id, started] of [['ER-0000000000000000EEE1', '2026-10-06T01:00:00+00:00'], ['ER-0000000000000000EEE2', '2026-10-06T03:00:00+00:00']]) {
      await send(page, { type: 'experiment.report.state', configuration_id: 7, generation: 0,
        report: { report_id: id, status: 'in_progress', started_at: started, protocol_id: PROTOCOL_ID, anomaly_count: 0, blocker_count: 0, event_count: 0, events: [] } });
    }
    await expect(page.locator('#last-report-id')).toHaveText('실험 기록 · In-gel digestion · 2026-10-06 12:00 · 2번째');
  });

  test('the experiment list uses the server title and count', async ({ page }) => {
    await openPage(page);
    const item = (id: string, started: string, sequence: number) => ({
      session_id: id, protocol_id: 'protocol-not-in-catalog', protocol_title: 'Western blot', status: 'active', current_step_label: '3',
      started_at: started, day_sequence: sequence, day_sequence_date: '2026-10-06',
    });
    await page.route('**/api/workspace/experiments', route => route.fulfill({ json: { experiments: [
      item('session-a', '2026-10-06T01:00:00+00:00', 1), item('session-b', '2026-10-06T04:30:00+00:00', 2)] } }));
    await page.evaluate(() => loadExperimentSessions(null));
    const options = page.locator('#experiment-session-select option');
    await expect(options.nth(1)).toHaveText(/^Western blot · 2026-10-06 10:00 · 1번째 · 3단계 · /);
    await expect(options.nth(2)).toHaveText(/^Western blot · 2026-10-06 13:30 · 2번째 · 3단계 · /);
  });

  test('the context card says the analysis state and the experimenter, never an approver', async ({ page }) => {
    await openPage(page, entry(true));
    await page.locator('#protocol-id').selectOption(PROTOCOL_ID);
    await page.evaluate(() => renderExperimentContext());
    await expect(page.locator('#experiment-context-version')).toContainText('분석 통과');
    await expect(page.locator('#experiment-context-experimenter')).not.toHaveText('');
    const card = await page.locator('.experiment-context-card').innerText();
    for (const word of ['승인', '검토자', '랩 관리자']) expect(card).not.toContain(word);
  });

  test('the server end of a cut-off playback replaces the screen guess on an earlier card', async ({ page }) => {
    await openPage(page);
    const turnState = (turn: number, revision: number, state: string) =>
      send(page, { type: 'turn.state', configuration_id: 7, turn_id: turn, generation: 0, revision, state });
    const card = (turn: number) => page.locator('#log .turn').filter({ has: page.locator('b', { hasText: new RegExp(`^Turn ${turn}$`, 'i') }) });
    let r = 0;
    for (const state of ['listening', 'transcribing', 'routing', 'synthesizing', 'playing']) await turnState(1, ++r, state);
    await expect(card(1).locator('.turn-status')).toHaveText('재생 중…');
    // The next turn starts first: the screen clears turn 1's progress.
    await turnState(2, 1, 'listening');
    await turnState(2, 2, 'transcribing');
    await expect(card(1).locator('.turn-status')).toHaveClass(/is-settled/);
    await expect(card(1).locator('.turn-settled')).toHaveCount(1);
    // Then the server's own end for turn 1 arrives (R6: cancelled after
    // cascade.playback.clear): the card follows the server.
    await turnState(1, ++r, 'cancelled');
    await expect(card(1).locator('.turn-status')).toHaveText('중단됨');
    await expect(card(1).locator('.turn-status')).not.toHaveClass(/is-settled/);
    await expect(card(1).locator('.turn-settled')).toHaveCount(0);
    await expect(card(1).locator('.turn-route')).toContainText('턴 상태 cancelled');
    // Later turns do not touch it again.
    await turnState(2, 3, 'routing');
    await expect(card(1).locator('.turn-status')).toHaveText('중단됨');

    // A barge-in candidate that was refused: the server ends the turn with
    // complete, and the card stops showing playback.
    r = 0;
    for (const state of ['listening', 'transcribing', 'routing', 'synthesizing', 'playing']) await turnState(3, ++r, state);
    await turnState(3, ++r, 'complete');
    await expect(card(3).locator('.turn-status')).toHaveText('완료');
    await expect(card(3)).not.toContainText('재생 중');
  });

  test('model knowledge is named "AI 일반 지식" in the turn card too', async ({ page }) => {
    await openPage(page);
    await send(page, { type: 'ready', research_capabilities: {
      external_text: { status: 'disabled' }, supplemental_model: { status: 'enabled' } } });
    await send(page, { type: 'speech.start', turn_id: 1, generation: 0 });
    await send(page, { type: 'transcript', turn_id: 1, generation: 0, text: '완충액은 왜 써?' });
    await send(page, { type: 'reply.complete', turn_id: 1, generation: 0, text: 'pH 를 일정하게 유지합니다.', answer_origin: 'supplemental_model_knowledge' });
    await send(page, { type: 'research.state', configuration_id: 7, turn_id: 1, generation: 0, status: 'running', phase: 'supplemental_model' });
    await send(page, { type: 'research.result', configuration_id: 7, turn_id: 1, generation: 0, status: 'success', terminal_status: 'success',
      primary_text: '일반적인 배경 설명', answer_origin: 'supplemental_model_knowledge', citations: [] });
    const turn = page.locator('#log .turn').first();
    await expect(turn.locator('.research-supplement > b')).toHaveText('AI 일반 지식');
    await expect(turn.locator('.answer-primary b').first()).toHaveText('AI 일반 지식');
    await expect(page.locator('#web-ref-title')).toHaveText('AI 일반 지식');
    await expect(turn).not.toContainText('일반 참고 설명');
  });

  test('an outside-PDF notice carried by reply.complete itself is drawn in its frame', async ({ page }) => {
    // Lane F puts display_document on reply.complete; without a delta first
    // the frame and the server's notice still show.
    await openPage(page);
    await send(page, { type: 'speech.start', turn_id: 1, generation: 0 });
    await send(page, { type: 'transcript', turn_id: 1, generation: 0, text: '중탄산암모늄은 왜 넣어?' });
    await send(page, { type: 'reply.complete', turn_id: 1, generation: 0, text: '완충액으로 쓰입니다.', answer_origin: 'supplemental_model_knowledge',
      display_document: { title: '1단계', sections: [{ kind: 'notice', text: 'PDF 밖 설명이니 유의' }, { kind: 'section', heading: '', text: '일반적으로 pH 를 맞추는 완충액으로 쓰입니다.' }] },
      limitations: ['outside_pdf_explanation'] });
    const turn = page.locator('#log .turn').first();
    await expect(turn.locator('.outside-pdf-label')).toHaveText('PDF 밖 설명 · AI 일반 지식');
    await expect(turn.locator('.outside-pdf')).toContainText('PDF 밖 설명이니 유의');
    await expect(turn.locator('.outside-pdf')).toContainText('완충액');
  });
});
