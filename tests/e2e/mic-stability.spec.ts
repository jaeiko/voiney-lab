import { test, expect, type Page } from '@playwright/test';

// Lane U2 (2026-10-06): the microphone is opened once per session and kept
// open; a device that goes away is reconnected once, and a failure shows a
// button instead of retrying forever. Chromium's fake capture device stands
// in for the microphone; the server's answers are fed to the page's own
// message handler, and the socket is a stand-in that only counts what the
// page sends. Nothing talks to a model or a speech provider.

test.use({
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  permissions: ['microphone'],
});

const PROTOCOL_ID = 'p-mic';
const REVISION_ID = 'r-mic';

declare global {
  interface Window {
    __gum: { calls: MediaStreamConstraints[]; fail: string | null };
    __extraDevices: MediaDeviceInfo[];
    __hideRealDevices: boolean;
    __sent: { text: string[]; audio: number };
  }
}

async function instrumentMediaDevices(page: Page, extraDevices: { deviceId: string; label: string }[] = []) {
  await page.addInitScript(extra => {
    const md = navigator.mediaDevices;
    const getUserMedia = md.getUserMedia.bind(md);
    const enumerateDevices = md.enumerateDevices.bind(md);
    window.__gum = { calls: [], fail: null };
    window.__extraDevices = extra.map(item => ({ kind: 'audioinput', groupId: item.deviceId, ...item, toJSON() { return this; } }) as unknown as MediaDeviceInfo);
    window.__hideRealDevices = false;
    md.getUserMedia = async (constraints?: MediaStreamConstraints) => {
      window.__gum.calls.push(JSON.parse(JSON.stringify(constraints ?? {})));
      if (window.__gum.fail) throw new DOMException('microphone unavailable', window.__gum.fail);
      // The fake capture device has its own id; the requested one is recorded above.
      const copy = JSON.parse(JSON.stringify(constraints ?? {}));
      if (copy.audio && typeof copy.audio === 'object') delete copy.audio.deviceId;
      return getUserMedia(copy);
    };
    md.enumerateDevices = async () => [...window.__extraDevices, ...(window.__hideRealDevices ? [] : await enumerateDevices())];
  }, extraDevices);
}

async function openSession(page: Page) {
  await page.waitForFunction(() => typeof onMessage === 'function' && typeof ensureAudio === 'function');
  await page.evaluate(async ([pid, rid]) => {
    window.__sent = { text: [], audio: 0 };
    socket = {
      readyState: WebSocket.OPEN,
      send(data: unknown) { if (typeof data === 'string') window.__sent.text.push(JSON.parse(data).type); else window.__sent.audio += 1; },
      close() {},
    } as unknown as WebSocket;
    pendingSessionConfiguration = { configuration_id: 7, mode: 'cascade', language: 'ko', protocol_id: pid };
    await onMessage({ data: JSON.stringify({ type: 'session.ready', configuration_id: 7, generation: 0, mode: 'cascade', language: 'ko', protocol_id: pid, revision_id: rid }) }, sessionGeneration, socket);
  }, [PROTOCOL_ID, REVISION_ID]);
  await expect.poll(() => page.evaluate(() => sessionActive)).toBe(true);
}

const micState = (page: Page) => page.evaluate(() => ({
  calls: window.__gum.calls.length,
  track: (micStream?.getAudioTracks()[0]?.readyState) ?? null,
  context: micContext?.state ?? null,
  audio: window.__sent.audio,
}));

const fixtureState = (paused: boolean, revision: number) => ({
  attached: true, protocol_id: PROTOCOL_ID, revision_id: REVISION_ID, display_name: 'Mic test protocol', development_only: true,
  readiness_status: 'guidance_ready', active: true, current_step_label: '2', current_step_id: 'step-2', total_steps: 5,
  at_final_step: false, block_reason: null, revision, display_summary: '2 Mix.', primary_summary: '2단계: 섞습니다.',
  source_language: 'en', spoken_summary: '2단계입니다.', warning_texts: [], warning_presentations: [], visual_assets: [],
  visual_status: 'unavailable', source_page_refs: [1], workflow_status: paused ? 'paused' : 'running',
});

test.describe('Microphone stays open (lane U2)', () => {
  test('getUserMedia is called once across turns, playback, pause and resume, and frames keep flowing', async ({ page }) => {
    await instrumentMediaDevices(page);
    await page.goto('/');
    await openSession(page);
    let state = await micState(page);
    expect(state).toMatchObject({ calls: 1, track: 'live', context: 'running' });
    await expect.poll(async () => (await micState(page)).audio).toBeGreaterThan(0);

    const send = (message: object) => page.evaluate(m => onMessage({ data: JSON.stringify(m) }, sessionGeneration, socket), message);
    for (let turn = 1; turn <= 3; turn += 1) {
      let revision = 0;
      for (const s of ['listening', 'transcribing', 'routing', 'synthesizing', 'playing']) {
        await send({ type: 'turn.state', configuration_id: 7, turn_id: turn, generation: 0, revision: ++revision, state: s });
      }
      await send({ type: 'speech.start', turn_id: turn, generation: 0 });
      await send({ type: 'transcript', turn_id: turn, generation: 0, text: `질문 ${turn}` });
      await send({ type: 'reply.delta', turn_id: turn, generation: 0, text: `답 ${turn}` });
      await send({ type: 'reply.complete', turn_id: turn, generation: 0, text: `답 ${turn}` });
      await send({ type: 'turn.state', configuration_id: 7, turn_id: turn, generation: 0, revision: ++revision, state: 'complete' });
    }
    // Playback: the last answer is played once through the output context.
    const played = await page.evaluate(async () => {
      lastReplayableAudio = new Int16Array(16000).fill(1200).buffer;
      return playLastReplayableAudio(null);
    });
    expect(played).toBe(true);
    const beforePause = (await micState(page)).audio;
    // Pause and resume come from the server's protocol state.
    await send({ type: 'protocol.fixture.state', configuration_id: 7, action: 'pause', state: fixtureState(true, 1) });
    await expect(page.locator('#rail-pause-session')).toHaveText('▶ 다시 시작');
    await expect.poll(async () => (await micState(page)).audio).toBeGreaterThan(beforePause);
    await send({ type: 'protocol.fixture.state', configuration_id: 7, action: 'resume', state: fixtureState(false, 2) });
    await expect(page.locator('#rail-pause-session')).toHaveText('일시정지');
    await send({ type: 'session.reset' });

    state = await micState(page);
    expect(state).toMatchObject({ calls: 1, track: 'live', context: 'running' });
    // Ending the session closes the microphone and does not reopen it.
    await page.evaluate(() => stopSession());
    await page.waitForTimeout(1200);
    expect(await page.evaluate(() => ({ calls: window.__gum.calls.length, stream: micStream }))).toEqual({ calls: 1, stream: null });
    await expect(page.locator('#mic-recovery')).toBeHidden();
  });

  test('a burst of devicechange events after the device went away reconnects exactly once', async ({ page }) => {
    await instrumentMediaDevices(page, [{ deviceId: 'other-mic', label: 'Other microphone' }]);
    await page.goto('/');
    await openSession(page);
    const before = await page.evaluate(() => micStream);
    await page.evaluate(() => {
      // The device in use leaves the list, and the browser says so three times.
      window.__hideRealDevices = true;
      for (let i = 0; i < 3; i += 1) navigator.mediaDevices.dispatchEvent(new Event('devicechange'));
    });
    await expect.poll(() => page.evaluate(() => window.__gum.calls.length), { timeout: 5000 }).toBe(2);
    await page.waitForTimeout(1500);
    const after = await micState(page);
    expect(after).toMatchObject({ calls: 2, track: 'live', context: 'running' });
    expect(await page.evaluate(previous => micStream !== previous, before)).toBe(true);
    await expect(page.locator('#mic-recovery')).toBeHidden();
    // Frames keep flowing from the new device into the same worklet.
    const sent = after.audio;
    await expect.poll(async () => (await micState(page)).audio).toBeGreaterThan(sent);
  });

  test('a track that ended is reconnected once, even with devicechange events alongside', async ({ page }) => {
    await instrumentMediaDevices(page);
    await page.goto('/');
    await openSession(page);
    await page.evaluate(() => {
      const track = micStream!.getAudioTracks()[0];
      track.stop();
      track.dispatchEvent(new Event('ended'));
      navigator.mediaDevices.dispatchEvent(new Event('devicechange'));
      navigator.mediaDevices.dispatchEvent(new Event('devicechange'));
    });
    await expect.poll(() => page.evaluate(() => window.__gum.calls.length), { timeout: 5000 }).toBe(2);
    await page.waitForTimeout(1500);
    expect(await micState(page)).toMatchObject({ calls: 2, track: 'live' });
  });

  test('a failed reconnect shows the reason and a button, retries no more, and the button reconnects', async ({ page }) => {
    await instrumentMediaDevices(page);
    await page.goto('/');
    await openSession(page);
    await page.evaluate(() => {
      window.__gum.fail = 'NotFoundError';
      const track = micStream!.getAudioTracks()[0];
      track.stop();
      track.dispatchEvent(new Event('ended'));
    });
    const recovery = page.locator('#mic-recovery');
    await expect(recovery).toBeVisible({ timeout: 5000 });
    await expect(recovery).toContainText('마이크를 찾지 못했습니다');
    await expect(page.locator('#mic-reconnect')).toHaveText('마이크 다시 연결');
    expect(await page.evaluate(() => window.__gum.calls.length)).toBe(2);
    // More device events do not start another automatic attempt.
    await page.evaluate(() => { for (let i = 0; i < 3; i += 1) navigator.mediaDevices.dispatchEvent(new Event('devicechange')); });
    await page.waitForTimeout(2500);
    expect(await page.evaluate(() => window.__gum.calls.length)).toBe(2);
    await expect(recovery).toBeVisible();

    await page.evaluate(() => { window.__gum.fail = null; });
    await page.locator('#mic-reconnect').click();
    await expect(recovery).toBeHidden();
    expect(await micState(page)).toMatchObject({ calls: 3, track: 'live', context: 'running' });
  });

  test('the chosen microphone is remembered, requested, and switched once during a session', async ({ page }) => {
    const devices = [{ deviceId: 'mic-built-in', label: 'MacBook 마이크' }, { deviceId: 'mic-iphone', label: 'iPhone 마이크' }];
    await instrumentMediaDevices(page, devices);
    await page.goto('/');
    const select = page.locator('#mic-device');
    await expect(select.locator('option[value="mic-iphone"]')).toHaveText('iPhone 마이크');
    await select.selectOption('mic-iphone');
    expect(await page.evaluate(() => localStorage.getItem('voiney-lab.mic-device'))).toContain('mic-iphone');

    await page.reload();
    await expect(select).toHaveValue('mic-iphone');
    await openSession(page);
    expect(await page.evaluate(() => (window.__gum.calls[0].audio as MediaTrackConstraints).deviceId)).toEqual({ exact: 'mic-iphone' });

    // Switching during the session opens the new device once.
    await select.selectOption('mic-built-in');
    await expect.poll(() => page.evaluate(() => window.__gum.calls.length)).toBe(2);
    expect(await page.evaluate(() => (window.__gum.calls[1].audio as MediaTrackConstraints).deviceId)).toEqual({ exact: 'mic-built-in' });
    expect(await micState(page)).toMatchObject({ calls: 2, track: 'live' });
  });

  test('a remembered microphone that is not connected falls back to the system default', async ({ page }) => {
    await instrumentMediaDevices(page, [{ deviceId: 'mic-built-in', label: 'MacBook 마이크' }]);
    await page.addInitScript(() => { try { localStorage.setItem('voiney-lab.mic-device', JSON.stringify({ deviceId: 'mic-airpods', label: 'AirPods' })); } catch (_) { /* storage may be off */ } });
    await page.goto('/');
    await expect(page.locator('#mic-device option:checked')).toContainText('연결 안 됨');
    await openSession(page);
    const audio = await page.evaluate(() => window.__gum.calls[0].audio as MediaTrackConstraints);
    expect(audio.deviceId).toBeUndefined();
    expect(audio.echoCancellation).toBe(true);
  });
});
