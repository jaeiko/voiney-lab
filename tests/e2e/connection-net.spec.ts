import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';

// Lane NET (2026-10-18): a lost connection, seen at once and put back. The
// voice socket is answered here by a stand-in server (Playwright's
// routeWebSocket): it starts the run, beats once a second on a clock 90 s
// ahead of the browser's, and then drops the connection, falls silent or is
// cut off by the browser going offline. The experiment's REST answers are
// given here too. Nothing talks to a model or a speech provider.
//
// Decision 1: the loss is seen within three seconds (close, offline,
// silence). Decision 2: the band, the step left on the screen, the
// microphone paused, nothing sent. Decision 3: the screen timer on the
// server's clock, its last minute and end told on the device. Decision 4:
// tries 1, 2, 4 s apart, one session.start that continues the experiment,
// the server's state taking over. Decision 5: the start carries when (on
// the server's clock) and why the connection was lost, which the server
// keeps in the record.

test.use({
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  permissions: ['microphone'],
});
test.describe.configure({ timeout: 60_000 });

const EXPERIMENT = 'experiment-net-e2e';
const PROTOCOL = 'p-net';
const REVISION = 'r-net';
// The stand-in server's clock runs this far ahead of the browser's.
const SERVER_AHEAD_MS = 90_000;

type Message = Record<string, any>;
type Connection = { ws: WebSocketRoute; openedAt: number; received: Message[]; closed: boolean; beat: ReturnType<typeof setInterval> | null; lastPing: Message | null; pingAt: number };

const serverNow = () => Date.now() + SERVER_AHEAD_MS;
const iso = (ms: number) => new Date(ms).toISOString();

class StandInServer {
  connections: Connection[] = [];
  version = 3;
  refusing = 0;
  refuseAll = false;
  silent = false;
  revision = 0;
  timerLeftMs: number | null = null;
  workflowStatus = 'running';
  noticeOnReconnect: string | null = null;

  timerState() {
    if (this.timerLeftMs === null) return undefined;
    const deadline = serverNow() + this.timerLeftMs;
    return {
      step: {
        state: this.timerLeftMs > 0 ? 'running' : 'expired', duration_seconds: 600,
        remaining_seconds: Math.max(0, Math.round(this.timerLeftMs / 1000)), elapsed_seconds: 600 - Math.round(this.timerLeftMs / 1000),
        step_index: 3, step_id: 'step-4', step_label: '4', deadline_at: iso(deadline), started_at: iso(deadline - 600_000), name: '10분', bound: 'exact',
      },
    };
  }

  state(stepLabel = '4') {
    return {
      attached: true, protocol_id: PROTOCOL, revision_id: REVISION, display_name: '연결 시험 프로토콜', development_only: false,
      readiness_status: 'guidance_ready', active: true, current_step_label: stepLabel, current_step_id: `step-${stepLabel}`, total_steps: 12,
      at_final_step: false, block_reason: null, revision: this.revision++, display_summary: 'Incubate at 37 °C.', primary_summary: '37 °C에서 10분 둡니다.',
      source_language: 'en', spoken_summary: null, warning_texts: ['Wear gloves.'], warning_presentations: [], visual_assets: [],
      visual_status: 'unavailable', source_page_refs: [4], workflow_status: this.workflowStatus, timers: this.timerState(),
    };
  }

  async attach(page: Page) {
    await page.routeWebSocket(/\/ws$/, ws => this.connect(ws));
    await page.route('**/api/workspace/experiments**', route => {
      const path = new URL(route.request().url()).pathname;
      const session = { session_id: EXPERIMENT, status: 'in_progress', version: this.version, protocol_id: PROTOCOL, protocol_revision_id: REVISION, current_step_id: 'step-4', current_step_label: '4', started_at: iso(Date.now() - 600_000) };
      if (path === '/api/workspace/experiments') return route.fulfill({ json: { experiments: [session] } });
      if (path === `/api/workspace/experiments/${EXPERIMENT}`) return route.fulfill({ json: { ...session, completed_steps: [], events: [] } });
      if (path === `/api/workspace/experiments/${EXPERIMENT}/timeline`) return route.fulfill({ json: { session, timeline: [], observation_count: 0, evidence_count: 0, recovery: { eligible: true, last_event_type: null, restored: { protocol_revision_id: REVISION, current_step_label: '4', completed_step_count: 3 }, not_restored: [], next_action: 'resume_voice_session' } } });
      return route.continue();
    });
  }

  connect(ws: WebSocketRoute) {
    const connection: Connection = { ws, openedAt: Date.now(), received: [], closed: false, beat: null, lastPing: null, pingAt: 0 };
    this.connections.push(connection);
    ws.onClose(() => { connection.closed = true; if (connection.beat) clearInterval(connection.beat); });
    if (this.refuseAll || this.refusing > 0) {
      if (this.refusing > 0) this.refusing -= 1;
      setTimeout(() => this.drop(connection), 50);
      return;
    }
    ws.send(JSON.stringify({ type: 'ready', sample_rate: 16000, pipelines: ['cascade'], frame_ms: 20, frame_bytes: 640, research_capabilities: {} }));
    ws.onMessage(raw => {
      if (typeof raw !== 'string') return;
      const message = JSON.parse(raw);
      connection.received.push(message);
      if (message.type === 'client.ping') { connection.lastPing = message; connection.pingAt = serverNow(); return; }
      if (message.type === 'session.start') this.start(connection, message);
    });
  }

  start(connection: Connection, start: Message) {
    const send = (message: Message) => { if (!connection.closed) connection.ws.send(JSON.stringify(message)); };
    this.version += 1;
    const configuration_id = start.configuration_id;
    send({ type: 'session.ready', configuration_id, generation: this.connections.length, mode: 'cascade', language: 'ko', input_language: 'ko', protocol_id: PROTOCOL, revision_id: REVISION, research_capabilities: {}, experiment_session_id: EXPERIMENT, experiment_session_version: this.version, experiment_recovered: Boolean(start.experiment_session_id), ...(start.reconnect ? { connection_restored: true } : {}) });
    send({ type: 'protocol.fixture.state', configuration_id, action: 'attached', state: this.state(start.reconnect ? '5' : '4'), screen: { next_step_sentence: '5단계: 상층액을 버립니다.' } });
    send({ type: 'session.started', state: 'IDLE', pipeline: 'cascade' });
    if (start.reconnect && this.noticeOnReconnect) {
      send({ type: 'protocol.timer.notice', configuration_id, generation: this.connections.length, notice_id: 'notice-net', notice_kind: 'timer_ended', text: this.noticeOnReconnect, spoken: false, chime: true, due_at: iso(serverNow()), step_id: 'step-4', step_label: '4', next_step_label: '5', timer: {} });
    }
    connection.beat = setInterval(() => {
      if (this.silent || connection.closed) return;
      const ping = connection.lastPing;
      send({ type: 'server.heartbeat', server_time_ms: serverNow(), timer_last_minute_from_seconds: 300, timer_last_minute_seconds: 60, ...(ping ? { ping_seq: ping.seq, ping_sent_at_ms: ping.sent_at_ms, ping_received_ms: connection.pingAt } : {}) });
    }, 1000);
  }

  drop(connection: Connection) {
    if (connection.closed) return;
    connection.closed = true;
    if (connection.beat) clearInterval(connection.beat);
    void connection.ws.close({ code: 1011, reason: 'stand-in server dropped the connection' });
  }

  dropAll() { for (const connection of this.connections) this.drop(connection); }
  last() { return this.connections[this.connections.length - 1]; }
}

async function openRun(page: Page, server: StandInServer) {
  await server.attach(page);
  await page.goto('/');
  // The page opens its socket on load; a session starts on it, as the start button's does.
  await page.waitForFunction(() => typeof onMessage === 'function' && typeof voineyConnection === 'object' && socket?.readyState === WebSocket.OPEN);
  await page.evaluate(async protocolId => {
    pendingSessionConfiguration = { configuration_id: ++nextConfigurationId, mode: 'cascade', language: 'ko', protocol_id: protocolId };
    sendControl('session.start', pendingSessionConfiguration);
  }, PROTOCOL);
  await expect.poll(() => page.evaluate(() => sessionActive)).toBe(true);
  await expect(page.locator('body')).toHaveAttribute('data-view', 'during');
  await expect(page.locator('#procedure-primary')).toContainText('37 °C에서 10분 둡니다.');
}

const band = (page: Page) => page.locator('#connection-band');
const sentTypes = (connection: Connection) => connection.received.map(message => message.type).filter(type => type !== 'client.ping');

test.describe('A lost connection, seen at once and put back (lane NET)', () => {
  test('a dropped connection: the band within three seconds, the run kept read only, tries 1 and 2 s apart, the server takes over', async ({ page }) => {
    const server = new StandInServer();
    server.noticeOnReconnect = '연결이 끊긴 동안 4단계 10분 타이머는 2분 전에 끝났어요. 다음은 5단계, 상층액을 버립니다.';
    await openRun(page, server);
    const first = server.last();
    // Two pings answered: the page has measured the server's clock.
    await page.waitForTimeout(2500);

    server.refusing = 2;
    const lostAt = Date.now();
    server.dropAll();
    await expect(band(page)).toBeVisible({ timeout: 3000 });
    await expect(band(page)).toContainText('연결이 끊겼어요 — 다시 연결 중');
    await expect(band(page)).toContainText('마이크 멈춤 · 연결되면 다시 들어요');
    await expect(band(page)).toContainText('화면을 켜 두세요');
    await expect(page.locator('body')).toHaveAttribute('data-view', 'during');
    await expect(page.locator('#procedure-primary')).toContainText('37 °C에서 10분 둡니다.');
    await expect(page.locator('#run-next-step')).toContainText('상층액을 버립니다');
    await expect(page.locator('#procedure-warning')).toContainText('Wear gloves.');
    await expect(page.locator('#run-mic')).toHaveText('마이크 멈춤 · 연결되면 다시 들어요');
    expect(await page.evaluate(() => micStream?.getAudioTracks().every(track => !track.enabled))).toBe(true);

    // Read only: a pressed control and a command sent go nowhere, now or later.
    await page.evaluate(() => { sendControl('workflow.pause', {}); document.querySelector<HTMLButtonElement>('#rail-pause-session')?.click(); });

    // Tries 1, 2 and then 4 s apart; the first two are refused by the stand-in.
    await expect.poll(() => server.connections.length, { timeout: 15_000 }).toBe(4);
    const opened = server.connections.slice(1).map(connection => connection.openedAt);
    const gaps = opened.map((at, index) => at - (index ? opened[index - 1] : lostAt));
    expect(gaps[0]).toBeGreaterThan(700); expect(gaps[0]).toBeLessThan(2600);
    expect(gaps[1]).toBeGreaterThan(1600); expect(gaps[1]).toBeLessThan(3000);
    expect(gaps[2]).toBeGreaterThan(3500); expect(gaps[2]).toBeLessThan(5200);

    const taken = server.last();
    await expect.poll(() => taken.received.some(message => message.type === 'session.start')).toBe(true);
    const start = taken.received.find(message => message.type === 'session.start')!;
    expect(start.experiment_session_id).toBe(EXPERIMENT);
    expect(start.experiment_session_version).toBe(4);
    expect(start.reconnect.reason).toBe('socket_closed');
    // When it was lost, on the server's clock (the page measured it 90 s ahead).
    expect(Math.abs(start.reconnect.lost_at_ms - (lostAt + SERVER_AHEAD_MS))).toBeLessThan(1500);
    for (const connection of server.connections.slice(1)) {
      expect(sentTypes(connection).filter(type => !['session.start', 'client.audio_ready', 'client.audio_constraints'].includes(type))).toEqual([]);
    }
    expect(sentTypes(first)).not.toContain('workflow.pause');

    // The server's state takes the place of the screen's copy.
    await expect(page.locator('#procedure-progress')).toContainText('5단계');
    await expect(band(page)).toContainText('다시 연결됐어요');
    await expect(page.locator('#timer-notice')).toContainText('연결이 끊긴 동안 4단계 10분 타이머는 2분 전에 끝났어요.');
    expect(await page.evaluate(() => micStream?.getAudioTracks().every(track => track.enabled))).toBe(true);
    await expect(band(page)).toBeHidden({ timeout: 6000 });
    await expect(page.locator('#run-mic')).not.toHaveText('마이크 멈춤 · 연결되면 다시 들어요');
    expect(sentTypes(taken)).not.toContain('client.voice_calibration');
  });

  test('the browser going offline and a silent server are each seen within three seconds', async ({ page, context }) => {
    const server = new StandInServer();
    await openRun(page, server);
    await page.waitForTimeout(1500);
    server.refuseAll = true;
    await context.setOffline(true);
    await expect(band(page)).toBeVisible({ timeout: 1000 });
    await context.setOffline(false);
    server.refuseAll = false;
    await expect(band(page)).toContainText('다시 연결됐어요', { timeout: 20_000 });
    const back = server.last();
    expect(back.received.find(message => message.type === 'session.start')?.reconnect?.reason).toBe('browser_offline');
    await expect(band(page)).toBeHidden({ timeout: 6000 });

    // Silent: the socket stays open but nothing comes.
    await page.waitForTimeout(1500);
    server.silent = true;
    const quietFrom = Date.now();
    await expect(band(page)).toBeVisible({ timeout: 4000 });
    expect(Date.now() - quietFrom).toBeLessThan(4000);
    server.silent = false;
    await expect(band(page)).toContainText('다시 연결됐어요', { timeout: 20_000 });
    expect(server.last().received.find(message => message.type === 'session.start')?.reconnect?.reason).toBe('server_silent');
  });

  test('the screen timer counts on the server clock and tells its end on the device', async ({ page }) => {
    const server = new StandInServer();
    server.timerLeftMs = 70_000;
    await openRun(page, server);
    // On the server's clock (90 s ahead), not the browser's: about 1:08 left, not 2:38.
    await expect(page.locator('#run-timer-digits')).toHaveText(/^1:[01]\d$/, { timeout: 5000 });
    server.refuseAll = true;
    server.dropAll();
    await expect(band(page)).toBeVisible({ timeout: 3000 });
    await expect(page.locator('#run-timer-offline')).toHaveText('연결 끊김 중 화면 타이머');
    await expect(band(page)).toContainText('화면 타이머 · 1분 남았어요', { timeout: 14_000 });
    await expect(page.locator('#run-timer-digits')).toHaveText(/^(0:5\d|1:00)$/);
  });

  test('a timer ending while the connection is lost is told on the device', async ({ page }) => {
    const server = new StandInServer();
    server.timerLeftMs = 7_000;
    await openRun(page, server);
    // The page has measured the server's clock before the connection goes.
    await expect(page.locator('#run-timer-digits')).toHaveText(/^0:0\d$/, { timeout: 5000 });
    server.refuseAll = true;
    server.dropAll();
    await expect(band(page)).toBeVisible({ timeout: 3000 });
    await expect(band(page)).toContainText('화면 타이머 · 타이머가 끝났어요', { timeout: 8000 });
    await expect(page.locator('#run-timer-digits')).toHaveText('끝남');
    await expect(band(page)).not.toContainText('1분 남았어요');
  });
});
