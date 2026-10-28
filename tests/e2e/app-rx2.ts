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

export interface StepPlan { label: string; id: string; korean: string | null; source: string; timerSeconds?: number | null; timerLeft?: number | null; figure?: boolean }

export const STEPS: StepPlan[] = [
  { label: '4', id: 'step-4', korean: '젤 조각을 37 °C에서 10분 동안 둡니다.', source: '4. Incubate the gel pieces at 37 °C for 10 min.', timerSeconds: 600, timerLeft: 510, figure: true },
  { label: '5', id: 'step-5', korean: '상층액을 버리고 아세토나이트릴 100 µL를 넣습니다.', source: '5. Discard the supernatant and add 100 µL acetonitrile.', timerSeconds: 120, timerLeft: 50 },
];

export interface ProtocolPlan {
  key: string; id: string; title: string; filename: string; field: string; stepCount: number; steps: StepPlan[];
  /** A reading the stand-in server makes in place for the first step (lane KO2's translation_update), written for the walk. */
  inPlaceKorean: string;
}

// The protocols the walks run (rule 10 of the lane rules, 2026-10-26: a change is checked on protocols of three or more
// fields, chemistry and battery materials among them). The first three step sets are those of analysed documents: in-gel digestion (bio; the Korean
// is a reviewed translation, carrying the step's own "N단계:"), EAM 2.3 Analytical Solution Preparation (FDA, chemistry /
// analysis: steps with no number, no Korean stored), Soil nutrient analysis SOP 005 (Zenodo, chemistry / analysis: the
// source carries its own "1.", no Korean stored). The battery set repeats the repository's explicitly fictional parity
// fixture. No model is called: a step without a stored Korean shows its source.
export const PROTOCOLS: Record<string, ProtocolPlan> = {
  ingel: { key: 'ingel', id: PID, title: 'In-gel digestion', filename: 'in-gel-digestion.pdf', field: '생명', stepCount: 9, steps: STEPS, inPlaceKorean: STEPS[0].korean! },
  eam23: {
    key: 'eam23', id: 'p-eam-2-3', title: 'EAM 2.3 Analytical Solution Preparation', filename: 'EAM 2.3 Analytical Solution Preparation.pdf', field: '화학·분석', stepCount: 13,
    inPlaceKorean: '분석 시료를 분해 용기 라이너에 옮깁니다(라이너 벽에 묻지 않게 하고, 질량은 보통 0.001 g 이하까지 적어요).',
    steps: [
      { label: '', id: 'eam-step-1', korean: null, source: 'Transfer analytical portion into a digestion vessel liner (avoid placing material on liner walls; mass typically recorded to ≤0.001g).' },
      { label: '', id: 'eam-step-2', korean: null, source: 'Optional - Add 1 mL water. (See note below for highly reactive matrices)', timerSeconds: 1200, timerLeft: 45 },
    ],
  },
  soil: {
    key: 'soil', id: 'p-soil-sop-005', title: 'Soil nutrient analysis (EU RADIANT SOP 005)', filename: 'Soil nutrient analysis (EU RADIANT SOP 005).pdf', field: '화학·분석', stepCount: 16,
    inPlaceKorean: '체로 친 마른 흙 10 g을 잽니다.',
    steps: [
      { label: '1', id: 'soil-step-1', korean: null, source: '1. Weigh 10 g of sieved dry soil soil.' },
      { label: '2', id: 'soil-step-2', korean: null, source: '2. Add in a flask the soil sample and 100 mL of KCL (1M)', timerSeconds: 3600, timerLeft: 50 },
    ],
  },
  battery: {
    key: 'battery', id: 'p-fictional-battery-electrode', title: '배터리 전극 가짜 자료', filename: 'fictional-battery-electrode.pdf', field: '배터리·소재', stepCount: 2,
    inPlaceKorean: '슬러리 B를 80 µm 두께로 코팅합니다.',
    steps: [
      { label: '1', id: 'battery-step-1', korean: null, source: 'Coat slurry B at 80 µm.' },
      { label: '2', id: 'battery-step-2', korean: null, source: 'Record coating.' },
    ],
  },
};

export class StandIn {
  signedIn = false;
  started = false;
  ended = false;
  paused = false;
  revision = 0;
  step = 0;
  configurationId = 0;
  // The server's generation: session.ready's number, moved on by a joined utterance (lane HEAR) or a barge-in; a resume
  // must carry the current one (server.py's workflow.resume fence), a pause need not.
  generation = 0;
  report = 'ER-RX4-0000000000000001';
  feedback: Message[] = [];
  received: string[] = [];
  /** Every control the screen sent, in order (its type and the generation it carried). */
  controls: Message[] = [];
  settings: Record<string, string> = { confirm_mode: 'readback', question_timing: 'before_start', web_lookup: 'on', proactive_mode: 'all', ambient_mode: 'quiet', speaker_sensitivity: 'normal', agent_voice: 'default', voice_calibration: 'none', speaker_filter: 'off', voice_reference: 'none' };
  calibrations: Array<{ pcm_base64: string; mic_id: string | null }> = [];
  audioReady: Message[] = [];
  settingPuts: object[] = [];
  answers: object[] = [];
  safetyReads = 0;
  changeChoices: object[] = [];
  starts: Message[] = [];
  memos: string[] = [];
  photos: string[] = [];
  wrongVoiceMutations = 0;
  pendingVoiceEnd = false;
  voiceTurn = 100;
  socket: WebSocketRoute | null = null;
  private send: (message: Message) => void = () => undefined;

  protocol: ProtocolPlan = PROTOCOLS.ingel;

  constructor(options: { signedIn?: boolean; protocol?: keyof typeof PROTOCOLS } = {}) {
    this.signedIn = options.signedIn ?? false;
    this.protocol = PROTOCOLS[options.protocol ?? 'ingel'];
  }

  /** The catalog's row for the protocol this stand-in runs. */
  entry() {
    return { ...ENTRY, protocol_id: this.protocol.id, title: this.protocol.title, source_filename: this.protocol.filename, step_count: this.protocol.stepCount };
  }

  experiment() {
    return { session_id: SESSION, protocol_id: this.protocol.id, protocol_title: this.protocol.title, status: this.ended ? 'stopped' : 'in_progress', version: 3, current_step_label: this.protocol.steps[this.step].label, started_at: iso(Date.now() - 600_000), owner_principal_id: 'p-a-rx2' };
  }

  timeline() {
    const events: object[] = [{ event_type: 'session_started', step_label: '1', created_at: iso(Date.now() - 600_000) }];
    events.push({ event_id: 'ev-voice-1', event_type: 'observation_recorded', step_label: '3', created_at: iso(Date.now() - 300_000), observation: { content: '젤 조각이 투명해졌어요', capture_source: 'voice', category: 'note' } });
    this.memos.forEach((content, at) => events.push({ event_id: `ev-memo-${at}`, event_type: 'observation_recorded', step_label: this.protocol.steps[this.step].label, created_at: iso(Date.now()), observation: { content, capture_source: 'manual', category: 'note' } }));
    this.photos.forEach((name, at) => events.push({ event_id: `ev-photo-${at}`, event_type: 'evidence_attached', step_label: this.protocol.steps[this.step].label, created_at: iso(Date.now()), evidence: { evidence_id: `e${at}`, original_filename: name, media_type: 'image/png' } }));
    return { session: this.experiment(), timeline: events, observation_count: 1 + this.memos.length, evidence_count: this.photos.length };
  }

  state() {
    const plan = this.protocol.steps[this.step];
    const step = plan.timerSeconds
      ? this.pausedTimer
        ? { state: 'paused', duration_seconds: plan.timerSeconds, remaining_seconds: plan.timerLeft, elapsed_seconds: plan.timerSeconds - (plan.timerLeft ?? 0), step_id: plan.id, step_label: plan.label, deadline_at: null, paused_at: iso(Date.now()), name: `${Math.round(plan.timerSeconds / 60)}분`, bound: 'exact' }
        : { state: 'running', duration_seconds: plan.timerSeconds, remaining_seconds: plan.timerLeft, elapsed_seconds: plan.timerSeconds - (plan.timerLeft ?? 0), step_id: plan.id, step_label: plan.label, deadline_at: iso(Date.now() + (plan.timerLeft ?? 0) * 1000), name: `${Math.round(plan.timerSeconds / 60)}분`, bound: 'exact' }
      : null;
    return {
      attached: true, protocol_id: this.protocol.id, revision_id: REVISION, source_sha256: SOURCE, display_name: this.protocol.title, development_only: false,
      readiness_status: 'guidance_ready', active: true, current_step_label: plan.label, current_step_id: plan.id, total_steps: this.protocol.stepCount,
      // As the merged server's state() (lane KO2): primary_summary is the step's checked Korean with its own "N단계: " taken
      // off, or null; translation_missing says there is none yet (the screen shows the source with "번역 준비 중").
      at_final_step: false, block_reason: null, revision: this.revision++, display_summary: plan.source, primary_summary: this.korean(plan),
      translation_missing: this.korean(plan) === null, spoken_summary: this.korean(plan) ?? plan.source, source_language: 'en',
      visual_assets: [], visual_status: plan.figure ? 'available' : 'unavailable', source_page_refs: [2],
      workflow_status: this.paused ? 'paused' : 'running', timers: { step, experiment: { state: 'running', started_at: iso(Date.now() - 600_000), ended_at: null, elapsed_seconds: 600 } },
    };
  }

  screen() {
    const steps = this.protocol.steps;
    const plan = steps[this.step];
    const figures = plan.figure
      ? [{ kind: 'source_figure', protocol_id: this.protocol.id, revision_id: REVISION, source_document_id: SOURCE, source_page: 2, figure_id: 'source-figure-2-1', sha256: 'd'.repeat(64), caption: 'Figure 1. 젤 조각을 담은 튜브', label: '원문 2쪽 그림 1', mime_type: 'image/png', url: `/api/protocols/${this.protocol.id}/revisions/${REVISION}/figures/source-figure-2-1` }]
      : [];
    // As the merged server's next_step_sentence (lane KO2): the Korean with its one step label, else the source line, with
    // next_step_translation_missing saying so; translation_pending while a reading is being made.
    const following = steps[this.step + 1];
    const korean = following ? this.korean(following) : null;
    const next = following ? (korean ? `${following.label}단계: ${korean}` : following.source) : null;
    return { next_step_sentence: next, next_step_translation_missing: Boolean(following) && korean === null, translation_pending: this.translating, source_figures: figures };
  }

  pushState(action = 'state') {
    this.send({ type: 'protocol.fixture.state', configuration_id: this.configurationId, action, state: this.state(), screen: this.screen() });
  }

  /** Readings the server made in place after the state went out (lane KO2's _queue_screen_korean), by step id. */
  readings: Record<string, string> = {};
  translating = false;

  /** A step's Korean as the server holds it now: the stored reading, else one made in place, else none. */
  korean(plan: StepPlan): string | null {
    return plan.korean ?? this.readings[plan.id] ?? null;
  }

  /** Lane KO2: the screen's sentences are being read in place (translation_pending), the state already sent says missing. */
  startTranslating() {
    this.translating = true;
    this.pushState('state');
  }

  /** Lane KO2: a checked reading arrived -- the same state again (action translation_update), now with the Korean. */
  translated(readings: Record<string, string>) {
    Object.assign(this.readings, readings);
    this.translating = false;
    this.pushState('translation_update');
  }

  /** Lane HEAR2, decision 3: "타이머 일시정지해 줘" -- the step's timer stands where it was (no deadline, its remaining seconds). */
  pausedTimer = false;
  pauseTimer() {
    this.pausedTimer = true;
    this.pushState('timer_pause');
  }

  /** The server moves to the next step (as a "다음 단계" turn would). */
  nextStep() {
    this.step = Math.min(this.protocol.steps.length - 1, this.step + 1);
    this.pushState('next');
  }

  /** A server-authoritative result for a spoken command. Speech recognition is outside this browser contract test. */
  voice(words: '화면 크게' | '보통 화면' | '다음' | '실험 일시정지' | '다시 시작' | '종료' | '네') {
    const turnId = ++this.voiceTurn;
    this.send({ type: 'speech.start', configuration_id: this.configurationId, turn_id: turnId, generation: this.generation });
    this.send({ type: 'transcript', configuration_id: this.configurationId, turn_id: turnId, generation: this.generation, text: words });
    if (words === '화면 크게' || words === '보통 화면') {
      this.send({ type: 'screen.display_mode', configuration_id: this.configurationId, turn_id: turnId, generation: this.generation, mode: words === '화면 크게' ? 'far' : 'normal' });
      return;
    }
    if (words === '다음') {
      if (this.step >= this.protocol.steps.length - 1) this.wrongVoiceMutations += 1;
      else this.nextStep();
      return;
    }
    if (words === '실험 일시정지') {
      if (this.paused) this.wrongVoiceMutations += 1;
      else {
        this.paused = true;
        this.send({ type: 'playback.stop', configuration_id: this.configurationId, turn_id: turnId, generation: this.generation, reason: 'paused' });
        this.pushState('pause');
      }
      return;
    }
    if (words === '다시 시작') {
      if (!this.paused) this.wrongVoiceMutations += 1;
      else { this.paused = false; this.pushState('resume'); }
      return;
    }
    if (words === '종료') {
      if (this.pendingVoiceEnd) this.wrongVoiceMutations += 1;
      else this.pendingVoiceEnd = true;
      return;
    }
    if (!this.pendingVoiceEnd) {
      this.wrongVoiceMutations += 1;
      return;
    }
    this.pendingVoiceEnd = false;
    this.ended = true;
    this.send({ type: 'experiment.ended', configuration_id: this.configurationId, turn_id: turnId, generation: this.generation, status: 'stopped', report: 'saved' });
    this.send({ type: 'session.stopped', configuration_id: this.configurationId });
  }

  /** A joined utterance or a barge-in: the server's generation moves on, announced by a speech.start with the new number
      (server.py's _join_continuation and the superseding turn), here let go again without words. */
  supersede() {
    this.generation += 1;
    this.send({ type: 'speech.start', configuration_id: this.configurationId, turn_id: 41 + this.generation, generation: this.generation });
    this.send({ type: 'speech.rejected', configuration_id: this.configurationId, turn_id: 41 + this.generation, generation: this.generation, reason: 'too_short' });
  }

  /** The server dropped a sound it could not use (lane HEAR's speech.dropped). */
  misheard() {
    // Lane HEAR2, decision 4: the drop's notice and its reason in plain words (shown, not said).
    this.send({ type: 'speech.dropped', configuration_id: this.configurationId, turn_id: 41, generation: 1, reason: 'empty_transcript', reason_ko: '받아 적은 말이 없어요', duration_ms: 640, level_db: -31.2, paused: false, during_playback: false, notice: '잘 안 들렸어요' });
  }

  /** The last control of a type the screen sent, with the generation it carried. */
  lastControl(type: string): Message | undefined {
    return [...this.controls].reverse().find((item) => item.type === type);
  }

  /** The server's side of the socket goes away (a lost connection; connection.js tries again). */
  async drop() {
    await this.socket?.close();
  }

  async attach(page: Page) {
    await page.routeWebSocket(/\/ws$/, (ws) => this.connect(ws));
    // A glob, not a predicate: only /api/ requests are intercepted, so the AudioWorklet module loads from the server (Playwright
    // fails a worklet module it intercepts -- lane NET's note).
    await page.route('**/api/**', (route: Route) => {
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
      // Lane HEAR's measurement (docs/FEATURES.md F8): the level of what was read, kept as voice_reference.
      if (path === '/api/voice/calibration') {
        const body = request.postDataJSON();
        this.calibrations.push(body);
        this.settings.voice_calibration = 'measured';
        this.settings.voice_reference = `v1:-23.4:${Math.floor(Date.now() / 1000)}:${body.mic_id || 'unknown'}`;
        return json({ state: 'measured', level_db: -23.4, measured_at: new Date().toISOString(), mic_id: body.mic_id || 'unknown', speaker_filter: this.settings.speaker_filter, max_age_days: 14 });
      }
      if (path === '/api/voice/preview') return route.fulfill({ status: 200, contentType: 'audio/wav', body: silenceWav() });
      const id = this.protocol.id;
      if (path === '/api/protocols') return json({ protocols: [this.entry()] });
      if (path === `/api/protocols/${id}`) return json({ ...this.entry(), analysis_run: { state: 'passed', failure_code: null, reanalysis: false } });
      if (path === `/api/protocols/${id}/review`) return json({ ...REVIEW, protocol_id: id });
      if (path === `/api/protocols/${id}/start-check`) return json({ ...CHECK, protocol_id: id });
      if (path === `/api/protocols/${id}/start-check/answers`) { this.answers.push(request.postDataJSON()); return json({ proceed: true, message_ko: '', followups: [] }); }
      if (path === `/api/protocols/${id}/start-check/safety`) { this.safetyReads += 1; return json({ ready: true, target: TARGET }); }
      if (path === `/api/protocols/${id}/start-check/changes`) {
        if (request.method() === 'POST') { this.changeChoices.push(request.postDataJSON()); return json({ change_set_id: 'run-changes-0123456789abcdef01234567', source: 'wizard', changes: [] }); }
        return json({ ...CHANGES, protocol_id: id });
      }
      if (path.startsWith(`/api/protocols/${id}/revisions/${REVISION}/figures/`)) return route.fulfill({ status: 200, contentType: 'image/png', body: FIGURE });
      if (path === '/api/workspace/session') return json({ display_name: '김재준', principal_id: 'p-a-rx2' });
      if (path === '/api/workspace/experiments') return json({ experiments: this.started && !this.ended ? [this.experiment()] : [] });
      if (path === `/api/workspace/experiments/${SESSION}`) return json(this.experiment());
      if (path === `/api/workspace/experiments/${SESSION}/timeline`) return json(this.timeline());
      if (path === `/api/workspace/experiments/${SESSION}/observations`) { this.memos.push(String(request.postDataJSON().content)); return json({ ok: true }); }
      if (path === `/api/workspace/experiments/${SESSION}/evidence`) { this.photos.push(new URL(request.url()).searchParams.get('filename') || 'photo'); return json({ ok: true }); }
      // Lane LOG's end-of-experiment answer, as server.py's post_pilot_feedback takes it (lane RX4 adds the chips as tags).
      if (path === '/api/pilot/feedback') {
        const body = request.postDataJSON();
        if (body.report_id !== this.report) return json({ detail: 'experiment report unavailable' }, 404);
        this.feedback.push(body);
        return json({ saved: true }, 201);
      }
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
      this.controls.push(message);
      if (message.type === 'client.audio_ready') {
        this.audioReady.push(message);
        // Lane HEAR decision 2: what the run's filter is, from the setting, the measurement and this microphone.
        const reference = /^v1:[^:]+:\d+:(.+)$/.exec(this.settings.voice_reference);
        const state = this.settings.speaker_filter !== 'on' ? ['inactive', 'off'] : !reference ? ['inactive', 'not_measured'] : reference[1] !== message.mic_id ? ['inactive', 'stale_device'] : ['active', 'on'];
        this.send({ type: 'speaker.filter', configuration_id: this.configurationId, state: state[0], reason: state[1], margin_db: state[0] === 'active' ? 12 : null, measured_at: null });
      }
      if (message.type === 'session.start') {
        this.started = true;
        this.starts.push(message);
        this.configurationId = message.configuration_id;
        // Each start moves the generation on (server.py's ListenerSession.start); session.ready carries the new number.
        this.generation += 1;
        this.send({ type: 'session.ready', configuration_id: message.configuration_id, generation: this.generation, mode: 'cascade', language: 'ko', input_language: message.input_language ?? 'ko', protocol_id: this.protocol.id, revision_id: REVISION, research_capabilities: {}, experiment_session_id: SESSION, experiment_session_version: 3 });
        this.pushState('attached');
        this.send({ type: 'session.started', state: 'IDLE', pipeline: 'cascade' });
        this.send({ type: 'session.greeting', configuration_id: message.configuration_id, turn_id: 1, generation: this.generation, greeting_id: 'g-1', text: GREETING });
        // The record this run writes (server.py's experiment.report.state), which the end screen's rating names.
        this.send({ type: 'experiment.report.state', configuration_id: message.configuration_id, turn_id: 1, generation: this.generation, report: { report_id: this.report, status: 'in_progress', protocol_title: this.protocol.title, started_at: iso(Date.now() - 600_000), session_id: SESSION, protocol_id: this.protocol.id, event_count: 1, events: [] } });
        beat = setInterval(() => this.send({ type: 'server.heartbeat', server_time_ms: Date.now() }), 1000);
      }
      if (message.type === 'workflow.pause') {
        this.paused = true;
        // Lane HEAR decision 7: the guidance playing stops with the pause.
        this.send({ type: 'playback.stop', configuration_id: this.configurationId, turn_id: 1, generation: this.generation, reason: 'paused' });
        this.pushState('pause');
      }
      if (message.type === 'workflow.resume') {
        // server.py's fence since the person's decision 3 of 2026-10-26: a resume of another configuration is refused as
        // stale; the generation is not compared (it moves on with a joined utterance or a barge-in).
        if (message.configuration_id !== this.configurationId) {
          this.send({ type: 'workflow.control.refused', action: 'resume', configuration_id: this.configurationId, generation: this.generation, reason: 'stale_configuration', message_ko: '현재 연결의 설정으로 다시 요청해 주세요.' });
        } else {
          this.paused = false;
          this.pushState('resume');
        }
      }
      if (message.type === 'session.stop') {
        // As server.py's session.stop: the saved experiment is stopped and the session let go; experiment.ended is not sent
        // here (it comes only with the turn that ends a run by voice), so the end the screen shows stands on the stop itself.
        this.ended = true;
        this.send({ type: 'experiment.session.state', state: { ...this.experiment(), status: 'stopped' } });
        this.send({ type: 'session.stopped' });
      }
    });
  }
}

/** The page's horizontal overflow (none is allowed). */
export async function overflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
}

/** The elements that reach past the window's right edge (for an overflow's message): tag, class, right edge, width. */
export async function overflowing(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const limit = document.documentElement.clientWidth + 0.5;
    const past = [...document.querySelectorAll<HTMLElement>('body *')].filter((el) => el.getBoundingClientRect().right > limit && el.getBoundingClientRect().width > 0);
    // The deepest ones: a container only as wide as the child that pushes it is not the cause.
    const leaves = past.filter((el) => !past.some((other) => other !== el && el.contains(other)));
    const name = (el: HTMLElement) => `${el.tagName.toLowerCase()}.${String(el.className).split(' ')[0]}${el.dataset.testid ? `[${el.dataset.testid}]` : ''}`;
    // Inside the main column: what reaches past its content box (the frame's own width follows the widest of these).
    const main = document.querySelector<HTMLElement>('main');
    const inner = main ? main.getBoundingClientRect().right - parseFloat(getComputedStyle(main).paddingRight) + 0.5 : limit;
    const wide = main ? [...main.querySelectorAll<HTMLElement>('*')].filter((el) => el.getBoundingClientRect().right > inner && el.getBoundingClientRect().width > 0) : [];
    const wideLeaves = wide.filter((el) => !wide.some((other) => other !== el && el.contains(other)));
    return [`viewport=${window.innerWidth} client=${document.documentElement.clientWidth} scroll=${document.documentElement.scrollWidth}`, ...leaves.slice(0, 4).map((el) => `${name(el)} right=${Math.round(el.getBoundingClientRect().right)} w=${Math.round(el.getBoundingClientRect().width)}`), 'in main:', ...wideLeaves.slice(0, 8).map((el) => `${name(el)} right=${Math.round(el.getBoundingClientRect().right)} w=${Math.round(el.getBoundingClientRect().width)} min=${el.scrollWidth} text="${(el.textContent || '').slice(0, 24)}"`)];
  });
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
