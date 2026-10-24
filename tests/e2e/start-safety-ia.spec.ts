import type { WebSocketRoute } from '@playwright/test';
import { test, expect, type Page, type Route, protocolRoute } from './routes';

// Lane IA stage ③ with lane SAFE's start rule (2026-10-21), on a phone and a desktop: the server
// starts a protocol only after this account finished the key-value check and acknowledged the
// safety notices. The sheet's [읽었어요] leaves that acknowledgement (POST start-check/safety with
// the GET start-check's target); a start without it is refused with the server's sentence and the
// sheet opens again at the safety step. The server here is a stand-in: page.route for the HTTP
// answers and routeWebSocket for the socket, refusing session.start until the acknowledgement came
// (contract-tested, not live-tested).

const PID = 'p-safe-ia-1';
const REVISION = 'pdf-1';
const ENTRY = {
  protocol_id: PID, title: 'Gamma (예시)', revision_id: REVISION, source_filename: 'gamma.pdf', source_sha256: 'c'.repeat(64),
  available_for_execution: true, analysis_status: 'structured_analysis_ready', lifecycle_state: 'ready', readiness_status: 'guidance_ready',
  step_count: 6, created_at: '2026-10-18T01:00:00+00:00', development_only: false, execution_blocker_codes: [], analysis_outdated_codes: [],
  analysis_outdated_ko: null, review_versions: [], current_review_version: null,
};
const REVIEW = {
  protocol_id: PID, title: 'Gamma (예시)', revision_id: REVISION, lifecycle_state: 'ready', readiness_status: 'guidance_ready',
  available_for_execution: true, analysis_available: true, source: { filename: 'gamma.pdf', sha256: 'a'.repeat(64), page_count: 3 },
  readiness: { status: 'guidance_ready', label: '안내 준비 완료', reasons: [] }, analysis_failure: null, constructs: [],
  execution_blockers: [], execution_notices: [], step_count: 6, sections: [], timers: [],
  safety_notices: [{ step_label: '2', step_id: 's2', source_page_number: 1, source_text: 'Wear gloves.', primary_text: '장갑을 끼세요.' }],
  pipeline: { stage: 'ready', stage_ko: '실행 가능', blocked: false, message: '분석 통과' },
};
const TARGET = { kind: 'analysis', version: null, event_id: null, analysis_id: 'an-1', revision_id: REVISION, analysis_revision_number: 1 };
const CHECK = {
  enabled: true, protocol_id: PID, target: TARGET, skipped_reason: null,
  questions: [{
    question_id: 'q1:step:s2:10min', position: 1, total: 1, progress_ko: '1 / 1', kind: 'time', kind_ko: '시간', step_label: '2', item_id: 'step:s2',
    question_ko: '2단계의 시간은(는) 원문에 얼마로 적혀 있나요?', source: { page: 1, excerpt: '2. Incubate for 10 min at 37 C.', lines: { page: 1, first_line: 4, last_line: 4 } },
    choices: [{ choice_id: 'c1', label_ko: '100 min' }, { choice_id: 'c2', label_ko: '10 min' }, { choice_id: 'c3', label_ko: '5 min' }],
  }],
};
const REFUSED = '안전 주의를 읽고 확인해 주세요.';

class StandIn {
  acknowledged: object[] = [];
  answers: object[] = [];
  received: Array<{ type: string; at: number }> = [];

  async attach(page: Page) {
    await page.route('**/api/protocols', (route: Route) => route.fulfill({ json: { protocols: [ENTRY] } }));
    await page.route(`**/api/protocols/${PID}/review`, (route: Route) => route.fulfill({ json: REVIEW }));
    await page.route(`**/api/protocols/${PID}/reagent-tables`, (route: Route) => route.fulfill({ json: { tables: [] } }));
    await page.route(`**/api/protocols/${PID}/start-check`, (route: Route) => route.fulfill({ json: CHECK }));
    await page.route(`**/api/protocols/${PID}/start-check/answers`, (route: Route) => {
      this.answers.push(route.request().postDataJSON());
      return route.fulfill({ json: { question_id: CHECK.questions[0].question_id, correct: true, proceed: true, chosen_label: '10 min', next_question_id: null, message_ko: '', followups: [] } });
    });
    await page.route(`**/api/protocols/${PID}/start-check/safety`, (route: Route) => {
      this.acknowledged.push({ body: route.request().postDataJSON(), at: Date.now() });
      return route.fulfill({ json: { ready: true, reason: null, target: TARGET, missing_question_ids: [], safety_acknowledged: true } });
    });
    // Lane EASY decision 2 (2026-10-24): "오늘 원문과 다르게 할 값" comes after the safety check; this stand-in offers none and keeps the skip.
    await page.route(`**/api/protocols/${PID}/start-check/changes`, (route: Route) => route.fulfill({ json: route.request().method() === 'POST' ? { change_set_id: 'run-changes-e2e', source: 'wizard', changes: [] } : { protocol_id: PID, target: TARGET, revision_id: 'pdf-1', limit: 12, values: [] } }));
    await page.routeWebSocket(/\/ws$/, ws => this.connect(ws));
  }

  connect(ws: WebSocketRoute) {
    ws.send(JSON.stringify({ type: 'ready', sample_rate: 16000, pipelines: ['cascade'], frame_ms: 20, frame_bytes: 640, research_capabilities: {} }));
    ws.onMessage(raw => {
      if (typeof raw !== 'string') return;
      const message = JSON.parse(raw);
      this.received.push({ type: message.type, at: Date.now() });
      if (message.type !== 'session.start') return;
      const configuration_id = message.configuration_id;
      if (!this.acknowledged.length) {
        ws.send(JSON.stringify({ type: 'session.configuration_required', configuration_id, mode: 'cascade', language: 'ko', protocol_id: PID, reason: 'safety_acknowledgement_required', message_ko: REFUSED }));
        return;
      }
      ws.send(JSON.stringify({ type: 'session.ready', configuration_id, generation: 1, mode: 'cascade', language: 'ko', input_language: 'ko', protocol_id: PID, revision_id: REVISION, research_capabilities: {} }));
    });
  }
}

async function openProtocol(page: Page) {
  await page.goto(protocolRoute(PID));
  await expect(page.locator('#protocol-detail-title')).toHaveText('Gamma (예시)');
  await expect(page.locator('#protocol-start')).toBeVisible();
}

test.describe('The start through the server-kept check (lane IA with lane SAFE)', () => {
  test('the key values, then the safety acknowledgement, then the start goes through', async ({ page }) => {
    const server = new StandIn();
    await server.attach(page);
    await openProtocol(page);
    await page.locator('#protocol-start').click();
    await expect(page.locator('#wizard-title')).toHaveText('2단계의 시간은(는) 원문에 얼마로 적혀 있나요?');
    await page.locator('#wizard-body .choice-card-choice', { hasText: '10 min' }).click();
    await expect(page.locator('#wizard-title')).toHaveText('안전 주의');
    await expect(page.locator('#wizard-body')).toContainText('장갑을 끼세요.');
    expect(server.acknowledged.length).toBe(0);
    await page.locator('#wizard-primary').click();
    // Lane EASY decision 2: "오늘 원문과 다르게 할 값", nothing to change.
    await expect(page.locator('#wizard-title')).toHaveText('오늘 원문과 다르게 할 값');
    await page.locator('#wizard-primary').click();
    await expect(page.locator('#wizard-primary')).toHaveText('이 프로토콜로 시작');
    expect(server.answers.length).toBe(1);
    expect(server.acknowledged.map(item => (item as { body: object }).body)).toEqual([{ target: TARGET }]);
    await page.locator('#wizard-primary').click();
    await expect.poll(() => server.received.filter(item => item.type === 'session.start').length).toBe(1);
    const startAt = server.received.find(item => item.type === 'session.start')!.at;
    expect((server.acknowledged[0] as { at: number }).at).toBeLessThanOrEqual(startAt);
    await expect(page.locator('#start-wizard')).toBeHidden();
    await expect(page.locator('#session-configuration-status')).not.toHaveText(REFUSED);
  });

  test('a start without the safety acknowledgement is refused with the server sentence', async ({ page }) => {
    const server = new StandIn();
    await server.attach(page);
    await openProtocol(page);
    await page.evaluate(() => startReviewedProtocol());
    await expect(page.locator('#session-configuration-status')).toHaveText(REFUSED);
    await expect(page.locator('#start-wizard')).toBeVisible();
    await expect(page.locator('#wizard-title')).toHaveText('안전 주의');
    await expect(page.locator('#wizard-note')).toHaveText(REFUSED);
    expect(server.acknowledged.length).toBe(0);
  });
});
