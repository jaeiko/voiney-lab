import type { BrowserContext, WebSocketRoute } from '@playwright/test';
import { test, expect, type Page, type Route, TAB } from './routes';

// Lane PRE decision 1 (2026-10-24, lane PERSONA F1): two accounts of one lab. One uploads a
// protocol; the other has the page open the whole time. Before, the open page kept the list it
// loaded -- "분석 중" -- and the start sheet refused the protocol until a reload. Now the list asks
// the server again (at once when a tab opens, every 5 s while an analysis runs), so the labmate
// starts the passed protocol without reloading. The server is a stand-in shared by both browsers
// (page.route for HTTP, routeWebSocket for the socket): the lab's catalog lives in this test
// (contract-tested, not live-tested).

const GAMMA = 'p-pre-gamma';
const THETA = 'p-pre-theta';
const REVISION = 'pdf-1';
const entry = (protocol_id: string, title: string, state: 'running' | 'ready') => ({
  protocol_id, title, revision_id: REVISION, source_filename: `${protocol_id}.pdf`, source_sha256: 'd'.repeat(64),
  available_for_execution: state === 'ready', analysis_status: 'structured_analysis_ready',
  lifecycle_state: state === 'ready' ? 'ready' : 'analyzing', readiness_status: 'analysis_required',
  step_count: 4, created_at: '2026-10-24T02:00:00+00:00', development_only: false, execution_blocker_codes: [], analysis_outdated_codes: [],
  analysis_outdated_ko: null, review_versions: [], current_review_version: null,
  analysis_run: { state: state === 'ready' ? 'passed' : 'analyzing', failure_code: null, reanalysis: false, requested_at: new Date(Date.now() - 5000).toISOString() },
});
const review = (protocol_id: string, title: string) => ({
  protocol_id, title, revision_id: REVISION, lifecycle_state: 'ready', readiness_status: 'analysis_required',
  available_for_execution: true, analysis_available: true, source: { filename: `${protocol_id}.pdf`, sha256: 'd'.repeat(64), page_count: 2 },
  readiness: { status: 'analysis_required', label: 'Protocol 분석 필요', reasons: [] }, analysis_failure: null, constructs: [],
  execution_blockers: [], execution_notices: [], step_count: 4, sections: [], timers: [],
  safety_notices: [{ step_label: '2', step_id: 's2', source_page_number: 1, source_text: 'Wear gloves.', primary_text: '장갑을 끼세요.' }],
  safety_scope: { usage_scope: 'source_protocol_only', source_protocol_only: true, notice: '기관 안전 문서 없음 · 원문 안전 주의만' },
  pipeline: { stage: 'ready', stage_ko: '실행 가능', blocked: false, message: '분석 통과' },
});
const TARGET = { kind: 'analysis', version: null, event_id: null, analysis_id: 'an-1', revision_id: REVISION, analysis_revision_number: 1 };
const account = (account_id: string, name: string) => ({
  mode: 'accounts', signed_in: true,
  account: { account_id, name, organization_id: 'lab-pilot', admin: false, entitlement: 'pilot', disabled: false, created_at: '2026-10-20T00:00:00+00:00' },
});

class Lab {
  protocols = [entry(GAMMA, 'Gamma (예시)', 'ready')];
  analysed = false;
  catalogAsks = new Map<string, number>();
  starts: Array<{ protocol_id: string }> = [];

  theta() { return this.protocols.find(item => item.protocol_id === THETA); }

  finishAnalysis() {
    this.analysed = true;
    this.protocols = this.protocols.map(item => item.protocol_id === THETA ? entry(THETA, 'Theta (예시)', 'ready') : item);
  }

  async attach(page: Page, who: string, name: string) {
    this.catalogAsks.set(who, 0);
    await page.route('**/api/auth/session', (route: Route) => route.fulfill({ json: account(`acct-${who}`, name) }));
    await page.route('**/api/workspace/session', (route: Route) => route.fulfill({ json: { principal_id: `acct-${who}`, display_name: name, organization_id: 'lab-pilot', workspaces: ['researcher'], authentication_method: 'accounts' } }));
    await page.route('**/api/protocols', (route: Route) => {
      this.catalogAsks.set(who, (this.catalogAsks.get(who) || 0) + 1);
      return route.fulfill({ json: { protocols: this.protocols } });
    });
    await page.route('**/api/protocols?filename=*', (route: Route) => {
      this.protocols = [...this.protocols, entry(THETA, 'Theta (예시)', 'running')];
      return route.fulfill({ status: 201, json: { protocol: this.theta(), deduplicated: false } });
    });
    await page.route(`**/api/protocols/${THETA}/analysis`, (route: Route) => route.fulfill({ status: 202, json: { analysis_request_accepted: true, analysis_run: { state: 'analyzing' } } }));
    await page.route(`**/api/protocols/${THETA}/analysis/status`, (route: Route) => route.fulfill({ json: this.analysed
      ? { state: 'review_required', worker_running: false, requested_at: this.theta()!.analysis_run.requested_at, pipeline: { message: '분석 통과' } }
      : { state: 'analyzing', worker_running: true, requested_at: this.theta()!.analysis_run.requested_at } }));
    for (const [id, title] of [[GAMMA, 'Gamma (예시)'], [THETA, 'Theta (예시)']]) {
      await page.route(`**/api/protocols/${id}/review`, (route: Route) => route.fulfill({ json: id === THETA && !this.analysed
        ? { ...review(id, title), available_for_execution: false, lifecycle_state: 'analyzing', analysis_available: false }
        : review(id, title) }));
      await page.route(`**/api/protocols/${id}/reagent-tables`, (route: Route) => route.fulfill({ json: { tables: [] } }));
      await page.route(`**/api/protocols/${id}/start-check`, (route: Route) => route.fulfill({ json: { enabled: true, protocol_id: id, target: TARGET, questions: [], skipped_reason: 'no_numeric_values' } }));
      await page.route(`**/api/protocols/${id}/start-check/safety`, (route: Route) => route.fulfill({ json: { ready: true, reason: null, target: TARGET, missing_question_ids: [], safety_acknowledged: true } }));
    }
    await page.routeWebSocket(/\/ws$/, ws => this.connect(ws));
  }

  connect(ws: WebSocketRoute) {
    ws.send(JSON.stringify({ type: 'ready', sample_rate: 16000, pipelines: ['cascade'], frame_ms: 20, frame_bytes: 640, research_capabilities: {} }));
    ws.onMessage(raw => {
      if (typeof raw !== 'string') return;
      const message = JSON.parse(raw);
      if (message.type !== 'session.start') return;
      this.starts.push({ protocol_id: message.protocol_id });
      ws.send(JSON.stringify({ type: 'session.ready', configuration_id: message.configuration_id, generation: 1, mode: 'cascade', language: 'ko', input_language: 'ko', protocol_id: message.protocol_id, revision_id: REVISION, research_capabilities: {} }));
    });
  }
}

const thetaRow = (page: Page) => page.locator('#protocol-list .protocol-row', { hasText: 'Theta (예시)' });

test.describe('A labmate\'s upload, started without a reload (lane PRE)', () => {
  test('one account uploads; the other, page open all along, sees it pass and starts it', async ({ page, browser, baseURL }) => {
    test.setTimeout(60_000);
    const lab = new Lab();
    let labmateLoads = 0;
    page.on('load', () => { labmateLoads += 1; });
    await lab.attach(page, 'labmate', '최수진');
    await page.goto(TAB.home);
    await expect(page.locator('#home-cards')).toContainText('새 실험 시작');
    expect(lab.catalogAsks.get('labmate')).toBeGreaterThanOrEqual(1);

    // The other account uploads in its own browser.
    const uploaderContext: BrowserContext = await browser.newContext({ baseURL });
    const uploader = await uploaderContext.newPage();
    await lab.attach(uploader, 'uploader', '김재준');
    await uploader.goto(TAB.upload);
    await uploader.locator('#protocol-pdf').setInputFiles({ name: 'theta.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 stand-in') });
    await expect(uploader.locator('#upload-step-progress')).toBeVisible();
    expect(lab.theta()?.available_for_execution).toBe(false);

    // The labmate opens the protocols tab: the list is asked at once and shows the running analysis.
    await page.locator('#tab-link-protocols').click();
    await expect(thetaRow(page)).toContainText('분석 중');

    // The analysis passes on the server; nobody on the labmate's side touches anything.
    lab.finishAnalysis();
    await expect(uploader.locator('#upload-result-title')).toHaveText('바로 쓸 수 있어요', { timeout: 10_000 });
    await expect(thetaRow(page)).toContainText('바로 쓸 수 있음', { timeout: 10_000 });
    await expect(thetaRow(page)).not.toContainText('분석 중');

    // And starts it: the protocol, the safety notice, the start -- no refusal on the way.
    await thetaRow(page).click();
    await expect(page.locator('#protocol-detail-title')).toHaveText('Theta (예시)');
    await page.locator('#protocol-start').click();
    await expect(page.locator('#wizard-title')).toHaveText('안전 주의');
    await page.locator('#wizard-primary').click();
    await expect(page.locator('#wizard-primary')).toHaveText('이 프로토콜로 시작');
    await expect(page.locator('#wizard-note')).not.toHaveText('분석을 통과한 프로토콜을 골라 주세요.');
    await page.locator('#wizard-primary').click();
    await expect.poll(() => lab.starts.map(item => item.protocol_id)).toEqual([THETA]);
    expect(labmateLoads).toBe(1);
    await uploaderContext.close();
  });
});
