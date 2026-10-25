import type { Page, Route, WebSocketRoute } from '@playwright/test';
import { deflateSync } from 'node:zlib';

// Lane RX2 (2026-10-25): a stand-in server for the new screen's walk -- login, the first guide, the start
// flow, the experiment screen (a step, a timer, records, a picture, a pause), the end. page.route answers
// the HTTP calls; routeWebSocket is the voice socket and answers as the server's contract does (session.ready,
// protocol.fixture.state, heartbeats, workflow.pause/resume, session.stop). Contract-tested, not live-tested:
// nothing talks to a model, a speech provider or a real server.

export const APP = '/app/';
export const PID = 'p-rx2-1';
export const REVISION = 'pdf-1';
export const SESSION = 's-rx2-1';
export const SOURCE = 'c'.repeat(64);
export const ACCOUNT = { account_id: 'a-rx2', name: '김재준', admin: false, entitlement: 'pilot', organization_id: 'lab-pilot', disabled: false };
export const GREETING = '실험 목적 이것은 시설에서 사용하는 인겔 소화 프로토콜을 따라 젤 조각을 자르고 단백질을 분해하는 과정이에요. 단계마다 말로 안내할게요.';
export const ENTRY = {
  protocol_id: PID, title: 'In-gel digestion', revision_id: REVISION, source_filename: 'in-gel-digestion.pdf', source_sha256: SOURCE,
  available_for_execution: true, analysis_status: 'structured_analysis_ready', lifecycle_state: 'ready', readiness_status: 'guidance_ready',
  step_count: 9, created_at: '2026-10-18T01:00:00+00:00', development_only: false, execution_blocker_codes: [], analysis_outdated_codes: [],
  analysis_outdated_ko: null, review_pending: null, review_versions: [], current_review_version: null,
};
const TARGET = { kind: 'analysis', version: null, event_id: null, analysis_id: 'an-1', revision_id: REVISION, analysis_revision_number: 1 };
const CHECK = {
  enabled: true, protocol_id: PID, target: TARGET, skipped_reason: null,
  questions: [
    { question_id: 'q1', progress_ko: '1 / 2', question_ko: '2단계의 시간은 원문에 얼마라고 적혀 있나요?', source: { page: 1, excerpt: '2. Incubate for 10 min at 37 °C.', lines: { first_line: 4, last_line: 4 } }, choices: [{ choice_id: 'c1', label_ko: '100 min' }, { choice_id: 'c2', label_ko: '10 min' }, { choice_id: 'c3', label_ko: '5 min' }] },
    { question_id: 'q2', progress_ko: '2 / 2', question_ko: '4단계의 온도는 원문에 얼마라고 적혀 있나요?', source: { page: 2, excerpt: '4. Dry the gel pieces at 56 °C.', lines: { first_line: 9, last_line: 10 } }, choices: [{ choice_id: 'd1', label_ko: '56 °C' }, { choice_id: 'd2', label_ko: '65 °C' }] },
  ],
};
const REVIEW = {
  protocol_id: PID, revision_id: REVISION, available_for_execution: true,
  safety_notices: [
    { step_label: '2', source_page_number: 1, source_text: 'Wear gloves and work in a fume hood.', primary_text: '장갑을 끼고 흄후드에서 작업하세요.' },
    { step_label: '6', source_page_number: 3, source_text: 'Acetonitrile is flammable. Keep away from flames.', primary_text: '아세토나이트릴은 불이 붙기 쉬워요. 불꽃에서 멀리 두세요.' },
  ],
  execution_notices: [],
  safety_scope: { usage_scope: 'source_protocol_only', source_protocol_only: true, notice: '기관 안전 문서가 없어 원문의 안전 주의만 보여요.' },
};
const CHANGES = {
  protocol_id: PID, target: TARGET, values: [
    { item_id: 'step:s4:time', step_id: 'step-4', step_label: '4', kind: 'time', kind_ko: '시간', shown_ko: '10 min', units_ko: 'min · s · h' },
    { item_id: 'step:s4:temp', step_id: 'step-4', step_label: '4', kind: 'temperature', kind_ko: '온도', shown_ko: '37 °C', units_ko: '°C' },
  ],
  last_time: null, limit: 12,
};
const VOICES = {
  provider: 'google_cloud', default_voice_id: 'ko-KR-Chirp3-HD-Charon', selected: 'default', preview_text_ko: '안녕하세요. 오늘 실험을 안내할게요.',
  voices: [{ voice_id: 'ko-KR-Chirp3-HD-Charon', label_ko: '차론 · 남성' }, { voice_id: 'ko-KR-Chirp3-HD-Kore', label_ko: '코레 · 여성' }],
};
const iso = (ms: number) => new Date(ms).toISOString();
type Message = Record<string, any>;

// A small picture for the step's source figure: a 480 x 300 PNG made here (a bench, three tubes).
function figurePng(): Buffer {
  const width = 480, height = 300;
  const rows: Buffer[] = [];
  for (let y = 0; y < height; y++) {
    const row = Buffer.alloc(1 + width * 3, 255);
    row[0] = 0;
    for (let x = 0; x < width; x++) {
      let ink = false;
      if (y > 250 && y < 262) ink = true;
      for (const cx of [140, 240, 340]) if (Math.abs(x - cx) < 18 && y > 90 && y < 250 && (Math.abs(x - cx) > 14 || y > 200)) ink = true;
      if (ink) row.fill(40, 1 + x * 3, 4 + x * 3);
    }
    rows.push(row);
  }
  const crcTable = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (data: Buffer) => { let c = 0xffffffff; for (const b of data) c = crcTable[(c ^ b) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (type: string, data: Buffer) => {
    const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type), data]);
    const sum = Buffer.alloc(4); sum.writeUInt32BE(crc(body));
    return Buffer.concat([length, body, sum]);
  };
  const header = Buffer.alloc(13); header.writeUInt32BE(width, 0); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(Buffer.concat(rows))), chunk('IEND', Buffer.alloc(0))]);
}
const FIGURE = figurePng();
// A tenth of a second of silence as a WAV, the voice preview's answer.
function silenceWav(): Buffer {
  const samples = 1600, data = Buffer.alloc(samples * 2), head = Buffer.alloc(44);
  head.write('RIFF', 0); head.writeUInt32LE(36 + data.length, 4); head.write('WAVE', 8); head.write('fmt ', 12); head.writeUInt32LE(16, 16);
  head.writeUInt16LE(1, 20); head.writeUInt16LE(1, 22); head.writeUInt32LE(16000, 24); head.writeUInt32LE(32000, 28); head.writeUInt16LE(2, 32); head.writeUInt16LE(16, 34);
  head.write('data', 36); head.writeUInt32LE(data.length, 40);
  return Buffer.concat([head, data]);
}

export interface StepPlan { label: string; id: string; korean: string; source: string; timerSeconds?: number | null; timerLeft?: number | null; figure?: boolean }

export const STEPS: StepPlan[] = [
  { label: '4', id: 'step-4', korean: '젤 조각을 37 °C에서 10분 동안 둡니다.', source: '4. Incubate the gel pieces at 37 °C for 10 min.', timerSeconds: 600, timerLeft: 510, figure: true },
  { label: '5', id: 'step-5', korean: '상층액을 버리고 아세토나이트릴 100 µL를 넣습니다.', source: '5. Discard the supernatant and add 100 µL acetonitrile.', timerSeconds: 120, timerLeft: 50 },
];

export class StandIn {
  signedIn = false;
  started = false;
  ended = false;
  paused = false;
  revision = 0;
  step = 0;
  configurationId = 0;
  received: string[] = [];
  settings: Record<string, string> = { confirm_mode: 'readback', question_timing: 'before_start', web_lookup: 'on', proactive_mode: 'all', ambient_mode: 'quiet', speaker_sensitivity: 'normal', agent_voice: 'default', voice_calibration: 'none' };
  settingPuts: object[] = [];
  answers: object[] = [];
  safetyReads = 0;
  changeChoices: object[] = [];
  starts: Message[] = [];
  memos: string[] = [];
  photos: string[] = [];
  socket: WebSocketRoute | null = null;
  private send: (message: Message) => void = () => undefined;

  constructor(options: { signedIn?: boolean } = {}) {
    this.signedIn = options.signedIn ?? false;
  }

  experiment() {
    return { session_id: SESSION, protocol_id: PID, protocol_title: 'In-gel digestion', status: this.ended ? 'stopped' : 'in_progress', version: 3, current_step_label: STEPS[this.step].label, started_at: iso(Date.now() - 600_000), owner_principal_id: 'p-a-rx2' };
  }

  timeline() {
    const events: object[] = [{ event_type: 'session_started', step_label: '1', created_at: iso(Date.now() - 600_000) }];
    events.push({ event_id: 'ev-voice-1', event_type: 'observation_recorded', step_label: '3', created_at: iso(Date.now() - 300_000), observation: { content: '젤 조각이 투명해졌어요', capture_source: 'voice', category: 'note' } });
    this.memos.forEach((content, at) => events.push({ event_id: `ev-memo-${at}`, event_type: 'observation_recorded', step_label: STEPS[this.step].label, created_at: iso(Date.now()), observation: { content, capture_source: 'manual', category: 'note' } }));
    this.photos.forEach((name, at) => events.push({ event_id: `ev-photo-${at}`, event_type: 'evidence_attached', step_label: STEPS[this.step].label, created_at: iso(Date.now()), evidence: { evidence_id: `e${at}`, original_filename: name, media_type: 'image/png' } }));
    return { session: this.experiment(), timeline: events, observation_count: 1 + this.memos.length, evidence_count: this.photos.length };
  }

  state() {
    const plan = STEPS[this.step];
    const step = plan.timerSeconds
      ? { state: 'running', duration_seconds: plan.timerSeconds, remaining_seconds: plan.timerLeft, elapsed_seconds: plan.timerSeconds - (plan.timerLeft ?? 0), step_id: plan.id, step_label: plan.label, deadline_at: iso(Date.now() + (plan.timerLeft ?? 0) * 1000), name: `${Math.round(plan.timerSeconds / 60)}분`, bound: 'exact' }
      : null;
    return {
      attached: true, protocol_id: PID, revision_id: REVISION, source_sha256: SOURCE, display_name: 'In-gel digestion', development_only: false,
      readiness_status: 'guidance_ready', active: true, current_step_label: plan.label, current_step_id: plan.id, total_steps: 9,
      at_final_step: false, block_reason: null, revision: this.revision++, display_summary: plan.source, primary_summary: plan.korean,
      spoken_summary: plan.korean, visual_assets: [], visual_status: plan.figure ? 'available' : 'unavailable', source_page_refs: [2],
      workflow_status: this.paused ? 'paused' : 'running', timers: { step },
    };
  }

  screen() {
    const plan = STEPS[this.step];
    const figures = plan.figure
      ? [{ kind: 'source_figure', protocol_id: PID, revision_id: REVISION, source_document_id: SOURCE, source_page: 2, figure_id: 'source-figure-2-1', sha256: 'd'.repeat(64), caption: 'Figure 1. 젤 조각을 담은 튜브', label: '원문 2쪽 그림 1', mime_type: 'image/png', url: `/api/protocols/${PID}/revisions/${REVISION}/figures/source-figure-2-1` }]
      : [];
    return { next_step_sentence: this.step + 1 < STEPS.length ? `${STEPS[this.step + 1].label}단계: ${STEPS[this.step + 1].korean}` : null, source_figures: figures };
  }

  pushState(action = 'state') {
    this.send({ type: 'protocol.fixture.state', configuration_id: this.configurationId, action, state: this.state(), screen: this.screen() });
  }

  /** The server moves to the next step (as a "다음 단계" turn would). */
  nextStep() {
    this.step = Math.min(STEPS.length - 1, this.step + 1);
    this.pushState('next');
  }

  /** The server dropped an utterance it took for another person's voice (speech.ignored, lane SP1). */
  misheard() {
    this.send({ type: 'speech.ignored', configuration_id: this.configurationId, turn_id: 41, generation: 1, reason: 'level_gate', during_playback: false });
  }

  async attach(page: Page) {
    await page.routeWebSocket(/\/ws$/, (ws) => this.connect(ws));
    await page.route((url) => url.pathname.startsWith('/api/'), (route: Route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const json = (body: object, status = 200) => route.fulfill({ status, json: body });
      if (path === '/api/auth/session') return json(this.signedIn ? { mode: 'accounts', signed_in: true, account: ACCOUNT } : { mode: 'accounts', signed_in: false, account: null });
      if (path === '/api/auth/login') { this.signedIn = true; return json({ signed_in: true, account: ACCOUNT }); }
      if (!this.signedIn) return json({ detail: 'authentication_required' }, 401);
      if (path === '/api/experimenter/settings') {
        if (request.method() === 'PUT') { const body = request.postDataJSON(); this.settingPuts.push(body); Object.assign(this.settings, body); }
        return json({ settings: this.settings });
      }
      if (path === '/api/voice/voices') return json({ ...VOICES, selected: this.settings.agent_voice });
      if (path === '/api/voice/preview') return route.fulfill({ status: 200, contentType: 'audio/wav', body: silenceWav() });
      if (path === '/api/protocols') return json({ protocols: [ENTRY] });
      if (path === `/api/protocols/${PID}`) return json({ ...ENTRY, analysis_run: { state: 'passed', failure_code: null, reanalysis: false } });
      if (path === `/api/protocols/${PID}/review`) return json(REVIEW);
      if (path === `/api/protocols/${PID}/start-check`) return json(CHECK);
      if (path === `/api/protocols/${PID}/start-check/answers`) { this.answers.push(request.postDataJSON()); return json({ proceed: true, message_ko: '', followups: [] }); }
      if (path === `/api/protocols/${PID}/start-check/safety`) { this.safetyReads += 1; return json({ ready: true, target: TARGET }); }
      if (path === `/api/protocols/${PID}/start-check/changes`) {
        if (request.method() === 'POST') { this.changeChoices.push(request.postDataJSON()); return json({ change_set_id: 'run-changes-0123456789abcdef01234567', source: 'wizard', changes: [] }); }
        return json(CHANGES);
      }
      if (path.startsWith(`/api/protocols/${PID}/revisions/${REVISION}/figures/`)) return route.fulfill({ status: 200, contentType: 'image/png', body: FIGURE });
      if (path === '/api/workspace/session') return json({ display_name: '김재준', principal_id: 'p-a-rx2' });
      if (path === '/api/workspace/experiments') return json({ experiments: this.started && !this.ended ? [this.experiment()] : [] });
      if (path === `/api/workspace/experiments/${SESSION}`) return json(this.experiment());
      if (path === `/api/workspace/experiments/${SESSION}/timeline`) return json(this.timeline());
      if (path === `/api/workspace/experiments/${SESSION}/observations`) { this.memos.push(String(request.postDataJSON().content)); return json({ ok: true }); }
      if (path === `/api/workspace/experiments/${SESSION}/evidence`) { this.photos.push(new URL(request.url()).searchParams.get('filename') || 'photo'); return json({ ok: true }); }
      return json({ detail: 'not_found' }, 404);
    });
  }

  connect(ws: WebSocketRoute) {
    this.socket = ws;
    let beat: ReturnType<typeof setInterval> | null = null;
    let closed = false;
    this.send = (message: Message) => { if (!closed) ws.send(JSON.stringify(message)); };
    ws.onClose(() => { closed = true; if (beat) clearInterval(beat); });
    this.send({ type: 'ready', sample_rate: 16000, pipelines: ['cascade'], frame_ms: 20, frame_bytes: 640, research_capabilities: {} });
    ws.onMessage((raw) => {
      if (typeof raw !== 'string') return;
      const message = JSON.parse(raw);
      if (message.type === 'client.ping') {
        this.send({ type: 'server.heartbeat', server_time_ms: Date.now(), ping_seq: message.seq, ping_sent_at_ms: message.sent_at_ms, ping_received_ms: Date.now() });
        return;
      }
      this.received.push(message.type);
      if (message.type === 'session.start') {
        this.started = true;
        this.starts.push(message);
        this.configurationId = message.configuration_id;
        this.send({ type: 'session.ready', configuration_id: message.configuration_id, generation: 1, mode: 'cascade', language: 'ko', input_language: 'ko', protocol_id: PID, revision_id: REVISION, research_capabilities: {}, experiment_session_id: SESSION, experiment_session_version: 3 });
        this.pushState('attached');
        this.send({ type: 'session.started', state: 'IDLE', pipeline: 'cascade' });
        this.send({ type: 'session.greeting', configuration_id: message.configuration_id, turn_id: 1, generation: 1, greeting_id: 'g-1', text: GREETING });
        beat = setInterval(() => this.send({ type: 'server.heartbeat', server_time_ms: Date.now() }), 1000);
      }
      if (message.type === 'workflow.pause' || message.type === 'workflow.resume') {
        this.paused = message.type === 'workflow.pause';
        this.pushState(this.paused ? 'pause' : 'resume');
      }
      if (message.type === 'session.stop') {
        this.ended = true;
        this.send({ type: 'experiment.session.state', state: { ...this.experiment(), status: 'stopped' } });
        this.send({ type: 'experiment.ended', configuration_id: this.configurationId, status: 'stopped', report: 'saved' });
        this.send({ type: 'session.stopped' });
      }
    });
  }
}

/** The page's horizontal overflow (none is allowed). */
export async function overflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

/** Whether an element is wholly inside the window as it stands (no scroll needed to see it). */
export async function inView(page: Page, selector: string): Promise<boolean> {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0 && box.top >= -0.5 && box.left >= -0.5 && box.bottom <= window.innerHeight + 0.5 && box.right <= document.documentElement.clientWidth + 0.5;
  }, selector);
}

/** Whether a box shows all its text: nothing hidden past its height or its width. */
export async function notClipped(page: Page, selector: string): Promise<{ ok: boolean; scroll: number; client: number }> {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel) as HTMLElement | null;
    if (!el) return { ok: false, scroll: -1, client: -1 };
    return { ok: el.scrollHeight <= el.clientHeight + 1 && el.scrollWidth <= el.clientWidth + 1, scroll: el.scrollHeight, client: el.clientHeight };
  }, selector);
}
