import type { WebSocketRoute } from '@playwright/test';
import { test, expect, type Page, type Route } from './routes';

// Lane TAB decision 1 (2026-10-23): one experiment from start to end on a tablet -- portrait 820x1180 and
// landscape 1180x820, Chromium with touch on (the 'Tablet portrait' and 'Tablet landscape' projects run this
// spec only). The walk: login -> the protocol -> the start sheet (the key value, the safety notices) -> the run
// (the step, the timer, the line of voice, a pause) -> another tab and back with 돌아가기 -> [종료] -> the end
// screen -> the records tab's report links. On every screen: no horizontal overflow, every visible tap target
// at least 44 x 44 px, an open sheet inside the screen. Every press is a tap.
//
// The server is a stand-in (contract-tested, not live-tested): page.route answers the HTTP calls the walk
// makes, routeWebSocket the voice socket (it starts the run, beats, pauses and stops it as told); Chromium's
// fake capture device is the microphone. Nothing talks to a model or a speech provider.

test.use({
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  permissions: ['microphone'],
});
test.describe.configure({ timeout: 90_000 });

const PID = 'p-tab-1';
const REVISION = 'pdf-1';
const SESSION = 's-tab-1';
const REPORT = 'ER-0000000000000000TAB1';
const ACCOUNT = { account_id: 'a-tab', name: '김재준', admin: false, entitlement: 'pilot', organization_id: 'lab-pilot', disabled: false };
const ENTRY = {
  protocol_id: PID, title: 'In-gel digestion (예시)', revision_id: REVISION, source_filename: 'in-gel.pdf', source_sha256: 'c'.repeat(64),
  available_for_execution: true, analysis_status: 'structured_analysis_ready', lifecycle_state: 'ready', readiness_status: 'guidance_ready',
  step_count: 6, created_at: '2026-10-18T01:00:00+00:00', development_only: false, execution_blocker_codes: [], analysis_outdated_codes: [],
  analysis_outdated_ko: null, review_versions: [], current_review_version: null,
  analysis_run: { state: 'passed', failure_code: null, reanalysis: false, requested_at: '2026-10-18T00:50:30+00:00' },
};
const REVIEW = {
  protocol_id: PID, title: 'In-gel digestion (예시)', revision_id: REVISION, lifecycle_state: 'ready', readiness_status: 'guidance_ready',
  available_for_execution: true, analysis_available: true, source: { filename: 'in-gel.pdf', sha256: 'a'.repeat(64), page_count: 3 },
  readiness: { status: 'guidance_ready', label: '안내 준비 완료', reasons: [] }, analysis_failure: null, constructs: [],
  execution_blockers: [], execution_notices: [], step_count: 6, sections: [], timers: [],
  safety_notices: [{ step_label: '2', step_id: 's2', source_page_number: 1, source_text: 'Wear gloves and work in a fume hood.', primary_text: '장갑을 끼고 흄후드에서 작업하세요.' }],
  safety_scope: { usage_scope: 'source_protocol_only', source_protocol_only: true, notice: '기관 안전 문서 없음 · 원문 안전 주의만' },
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
const SETTINGS = { settings: { confirm_mode: 'readback', question_timing: 'before_start', web_lookup: 'on', proactive_mode: 'all', ambient_mode: 'quiet', speaker_sensitivity: 'normal', agent_voice: 'default', voice_calibration: 'none' } };
const VOICES = {
  provider: 'google_cloud', default_voice_id: 'ko-KR-Chirp3-HD-Charon', selected: 'default', preview_text_ko: '안녕하세요',
  voices: [{ voice_id: 'ko-KR-Chirp3-HD-Charon', label_ko: '차론 · 남성' }, { voice_id: 'ko-KR-Chirp3-HD-Kore', label_ko: '코레 · 여성' }],
};
const iso = (ms: number) => new Date(ms).toISOString();
type Message = Record<string, any>;

class StandIn {
  signedIn = false;
  started = false;
  ended = false;
  paused = false;
  revision = 0;
  configurationId = 0;
  received: string[] = [];
  logins: object[] = [];

  experiment() {
    return { session_id: SESSION, protocol_id: PID, protocol_title: 'In-gel digestion (예시)', status: this.ended ? 'stopped' : 'in_progress', version: 3, current_step_label: '4', started_at: iso(Date.now() - 600_000) };
  }

  state() {
    const deadline = Date.now() + 510_000;
    return {
      attached: true, protocol_id: PID, revision_id: REVISION, display_name: 'In-gel digestion (예시)', development_only: false,
      readiness_status: 'guidance_ready', active: true, current_step_label: '4', current_step_id: 'step-4', total_steps: 6,
      at_final_step: false, block_reason: null, revision: this.revision++, display_summary: '4 Incubate the gel pieces at 37 °C for 10 min.',
      primary_summary: '4단계: 젤 조각을 37 °C에서 10분 둡니다.', source_language: 'en', spoken_summary: '4단계입니다.', warning_texts: ['Wear gloves.'],
      warning_presentations: [{ primary_text: '장갑을 끼세요.', source_text: 'Wear gloves.', source_page: 2, evidence_id: 'w1' }], visual_assets: [],
      visual_status: 'unavailable', source_page_refs: [2], workflow_status: this.paused ? 'paused' : 'running',
      timers: { step: { state: 'running', duration_seconds: 600, remaining_seconds: 510, elapsed_seconds: 90, step_index: 3, step_id: 'step-4', step_label: '4', deadline_at: iso(deadline), started_at: iso(deadline - 600_000), name: '10분', bound: 'exact' } },
    };
  }

  async attach(page: Page) {
    await page.routeWebSocket(/\/ws$/, ws => this.connect(ws));
    await page.route(url => url.pathname.startsWith('/api/'), (route: Route) => {
      const path = new URL(route.request().url()).pathname;
      const json = (body: object) => route.fulfill({ json: body });
      if (path === '/api/auth/session') return json(this.signedIn ? { mode: 'accounts', signed_in: true, account: ACCOUNT } : { mode: 'accounts', signed_in: false, account: null });
      if (path === '/api/auth/login') { this.logins.push(route.request().postDataJSON()); this.signedIn = true; return json({ signed_in: true, account: ACCOUNT }); }
      if (!this.signedIn) return route.fulfill({ status: 401, json: { detail: 'authentication_required' } });
      if (path === '/api/experimenter/settings') return json(SETTINGS);
      if (path === '/api/voice/voices') return json(VOICES);
      if (path === '/api/protocols') return json({ protocols: [ENTRY] });
      if (path === `/api/protocols/${PID}/review`) return json(REVIEW);
      if (path === `/api/protocols/${PID}/reagent-tables`) return json({ tables: [] });
      if (path === `/api/protocols/${PID}/analysis/status`) return json({ state: 'passed', worker_running: false });
      if (path === `/api/protocols/${PID}/start-check`) return json(CHECK);
      if (path === `/api/protocols/${PID}/start-check/answers`) return json({ question_id: CHECK.questions[0].question_id, correct: true, proceed: true, chosen_label: '10 min', next_question_id: null, message_ko: '', followups: [] });
      if (path === `/api/protocols/${PID}/start-check/safety`) return json({ ready: true, reason: null, target: TARGET, missing_question_ids: [], safety_acknowledged: true });
      if (path === '/api/workspace/session') return json({ display_name: '김재준', principal_id: 'p-a-tab' });
      if (path === '/api/workspace/experiments') return json({ experiments: this.started ? [this.experiment()] : [] });
      if (path === `/api/workspace/experiments/${SESSION}/timeline`) return json({ session: this.experiment(), timeline: [{ event_type: 'session_started', step_label: '1', created_at: iso(Date.now() - 600_000) }], observation_count: 0, evidence_count: 0 });
      if (path === `/api/workspace/experiments/${SESSION}/reports`) {
        const reports = this.ended ? [{ report_id: REPORT, session_id: SESSION, protocol_id: PID, protocol_title: 'In-gel digestion (예시)', status: 'stopped', started_at: iso(Date.now() - 600_000), ended_at: iso(Date.now()), anomaly_count: 0, blocker_count: 0, finalization_version: 1, development_only: false }] : [];
        return json({ session_id: SESSION, reports });
      }
      return route.continue();
    });
  }

  connect(ws: WebSocketRoute) {
    let beat: ReturnType<typeof setInterval> | null = null;
    let closed = false;
    const send = (message: Message) => { if (!closed) ws.send(JSON.stringify(message)); };
    ws.onClose(() => { closed = true; if (beat) clearInterval(beat); });
    send({ type: 'ready', sample_rate: 16000, pipelines: ['cascade'], frame_ms: 20, frame_bytes: 640, research_capabilities: {} });
    ws.onMessage(raw => {
      if (typeof raw !== 'string') return;
      const message = JSON.parse(raw);
      if (message.type === 'client.ping') {
        send({ type: 'server.heartbeat', server_time_ms: Date.now(), ping_seq: message.seq, ping_sent_at_ms: message.sent_at_ms, ping_received_ms: Date.now() });
        return;
      }
      this.received.push(message.type);
      if (message.type === 'session.start') {
        this.started = true;
        this.configurationId = message.configuration_id;
        send({ type: 'session.ready', configuration_id: message.configuration_id, generation: 1, mode: 'cascade', language: 'ko', input_language: 'ko', protocol_id: PID, revision_id: REVISION, research_capabilities: {}, experiment_session_id: SESSION, experiment_session_version: 3 });
        send({ type: 'protocol.fixture.state', configuration_id: message.configuration_id, action: 'attached', state: this.state(), screen: { next_step_sentence: '5단계: 상층액을 버립니다.' } });
        send({ type: 'session.started', state: 'IDLE', pipeline: 'cascade' });
        beat = setInterval(() => send({ type: 'server.heartbeat', server_time_ms: Date.now() }), 1000);
      }
      if (message.type === 'workflow.pause' || message.type === 'workflow.resume') {
        this.paused = message.type === 'workflow.pause';
        send({ type: 'protocol.fixture.state', configuration_id: this.configurationId, action: this.paused ? 'pause' : 'resume', state: this.state() });
      }
      if (message.type === 'session.stop') {
        this.ended = true;
        send({ type: 'experiment.session.state', state: { ...this.experiment(), status: 'stopped' } });
        send({ type: 'experiment.ended', configuration_id: this.configurationId, status: 'stopped', report: 'saved' });
        send({ type: 'session.stopped' });
      }
    });
  }
}

type Measured = { overflow: number; small: string[]; sheets: Array<{ name: string; inside: boolean; rect: number[] }> };

// What a finger meets on the screen now: the page's horizontal overflow, every visible tap target under
// 44 x 44 px (a checkbox or radio inside its label counts as the label), and each open sheet's box.
function measure(): Measured {
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const shown = (el: Element) => {
    const style = getComputedStyle(el), box = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity) !== 0 && box.width > 1 && box.height > 1 && !el.closest('[hidden],[aria-hidden="true"]');
  };
  const name = (el: Element) => `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''} "${(el.getAttribute('aria-label') || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40)}"`;
  const small: string[] = [];
  for (let el of Array.from(document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, summary, [role=button]'))) {
    const input = el as HTMLInputElement;
    if ((input.type === 'checkbox' || input.type === 'radio') && el.closest('label')) el = el.closest('label') as Element;
    if (el.classList.contains('visually-hidden-file-input') || !shown(el)) continue;
    const box = el.getBoundingClientRect();
    if (box.width < 44 || box.height < 44) small.push(`${name(el)} ${Math.round(box.width)}x${Math.round(box.height)}`);
  }
  const sheets: Measured['sheets'] = [];
  const wizardSheet = document.getElementById('wizard-sheet');
  const candidates: Array<[string, Element | null]> = [
    ['start sheet', wizardSheet?.dataset.mode === 'sheet' ? document.getElementById('wizard-sheet-panel') : null],
    ['settings sheet', document.getElementById('settings-sheet')],
    ['conversation sheet', document.getElementById('conversation-sheet')],
    ['figures sheet', document.getElementById('figures-sheet')],
  ];
  for (const [label, el] of candidates) {
    if (!el || !shown(el)) continue;
    if (label !== 'start sheet' && getComputedStyle(el).position !== 'fixed') continue;
    const box = el.getBoundingClientRect();
    sheets.push({ name: label, inside: box.left >= -0.5 && box.top >= -0.5 && box.right <= vw + 0.5 && box.bottom <= vh + 0.5, rect: [box.left, box.top, box.right, box.bottom].map(Math.round) });
  }
  return { overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth, small: Array.from(new Set(small)), sheets };
}

async function expectTabletScreen(page: Page, screen: string) {
  const measured = await page.evaluate(measure);
  expect(measured.overflow, `${screen}: horizontal overflow`).toBe(0);
  expect(measured.small, `${screen}: tap targets under 44 px`).toEqual([]);
  for (const sheet of measured.sheets) expect(sheet.inside, `${screen}: the ${sheet.name} at ${sheet.rect} is not inside the screen`).toBe(true);
}

test.describe('One experiment on a tablet (lane TAB)', () => {
  test('login, the start sheet, the run, another tab and back, the end and the report link -- all by touch', async ({ page }) => {
    const server = new StandIn();
    await server.attach(page);
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));

    // Login: the page of its own.
    await page.goto('/');
    await expect(page.locator('body')).toHaveAttribute('data-auth', 'required');
    await expect(page.locator('#wizard-name')).toBeVisible();
    await expectTabletScreen(page, 'login');
    await page.locator('#wizard-name').fill('김재준');
    await page.locator('#wizard-password').fill('correct horse');
    await page.locator('#wizard-primary').tap();
    await expect(page.locator('body')).not.toHaveAttribute('data-auth', 'required');
    expect(server.logins).toEqual([{ name: '김재준', password: 'correct horse' }]);
    // The first login offers the setup once, as a sheet that can be closed.
    await expect(page.locator('#wizard-sheet')).toBeVisible();
    await expectTabletScreen(page, 'first setup');
    await page.locator('#wizard-close').tap();
    await expect(page.locator('#wizard-sheet')).toBeHidden();

    // The protocols: the list, then the protocol's own screen.
    await page.locator('#tab-link-protocols').tap();
    await expect(page.locator('#protocol-list')).toContainText('In-gel digestion (예시)');
    await expectTabletScreen(page, 'protocol list');
    await page.locator('#protocol-list .protocol-row', { hasText: 'In-gel digestion (예시)' }).tap();
    await expect(page).toHaveURL(new RegExp(`#/protocols/${PID}$`));
    await expect(page.locator('#protocol-start')).toBeVisible();
    await expectTabletScreen(page, 'protocol screen');

    // The start sheet: the key value, the safety notices, the start.
    await page.locator('#protocol-start').tap();
    await expect(page.locator('#wizard-title')).toHaveText('2단계의 시간은(는) 원문에 얼마로 적혀 있나요?');
    await expectTabletScreen(page, 'start sheet · key value');
    await page.locator('#wizard-body .choice-card-choice', { hasText: '10 min' }).tap();
    await expect(page.locator('#wizard-title')).toHaveText('안전 주의');
    await expect(page.locator('#wizard-body')).toContainText('장갑을 끼고 흄후드에서 작업하세요.');
    await expectTabletScreen(page, 'start sheet · safety');
    await page.locator('#wizard-primary').tap();
    await expect(page.locator('#wizard-primary')).toHaveText('이 프로토콜로 시작');
    await expectTabletScreen(page, 'start sheet · start');
    await page.locator('#wizard-primary').tap();

    // The run: the step, the timer, the line of voice, the buttons.
    await expect(page.locator('body')).toHaveAttribute('data-view', 'during');
    await expect(page).toHaveURL(/#\/experiment$/);
    await expect(page.locator('#procedure-primary')).toContainText('젤 조각을 37 °C에서 10분 둡니다.');
    await expect(page.locator('#run-timer-digits')).toHaveText(/^\d{1,2}:\d{2}$/);
    await expect(page.locator('#run-timer-digits')).toBeInViewport();
    await expect(page.locator('#procedure-primary')).toBeInViewport();
    await expect(page.locator('.voice-console')).toBeInViewport();
    await expect(page.locator('#rail-pause-session')).toBeEnabled();
    expect(server.received).toContain('client.audio_ready');
    await expectTabletScreen(page, 'run');

    // A pause and the resume, by the rail's button and the card's.
    await page.locator('#rail-pause-session').tap();
    await expect(page.locator('#workflow-paused-banner')).toBeVisible();
    await expect(page.locator('#rail-pause-session')).toHaveText('▶ 다시 시작');
    await expectTabletScreen(page, 'run · paused');
    await page.locator('#workflow-resume-button').tap();
    await expect(page.locator('#workflow-paused-banner')).toBeHidden();
    expect(server.received.filter(type => type.startsWith('workflow.'))).toEqual(['workflow.pause', 'workflow.resume']);

    // The run's settings open as a sheet over it.
    await page.locator('#run-open-settings').tap();
    await expect(page.locator('#settings-sheet')).toBeVisible();
    await expectTabletScreen(page, 'run · settings sheet');
    await page.locator('#run-close-settings').tap();
    await expect(page.locator('#settings-sheet')).toBeHidden();

    // Another tab (the back gesture leaves the run's tab for the protocol's screen) and 돌아가기.
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`#/protocols/${PID}$`));
    await expect(page.locator('#run-return-band')).toBeVisible();
    await expect(page.locator('#run-return-text')).toHaveText('실험 중 · 4단계');
    await expectTabletScreen(page, 'another tab during the run');
    await page.locator('#run-return-link').tap();
    await expect(page).toHaveURL(/#\/experiment$/);
    await expect(page.locator('#run-return-band')).toBeHidden();
    await expect(page.locator('#procedure-primary')).toBeInViewport();
    await expectTabletScreen(page, 'back on the run');

    // [종료]: the server's end, then the end screen.
    await page.locator('#rail-stop-session').tap();
    await expect(page.locator('body')).toHaveAttribute('data-view', 'after');
    await expect(page.locator('#after-state')).toHaveText('실험 종료');
    await expectTabletScreen(page, 'end screen');
    expect(server.received).toContain('session.stop');

    // The records tab: the ended experiment and its report links.
    await page.locator('#tab-link-records').tap();
    const row = page.locator('#records-list .records-row').first();
    await expect(row).toContainText('In-gel digestion (예시)');
    await expectTabletScreen(page, 'records');
    await row.tap();
    await expect(page).toHaveURL(new RegExp(`#/records/${SESSION}$`));
    const links = page.locator('#records-report-list .records-report .report-actions a');
    await expect(links.first()).toHaveText('보고서 만들기 (Word)');
    await expect(links.first()).toHaveAttribute('href', `/api/experiment-reports/${REPORT}.docx`);
    await expectTabletScreen(page, 'record and its reports');

    expect(errors).toEqual([]);
  });
});
