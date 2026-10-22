import { test, expect, type Page, type Route, TAB, protocolRoute } from './routes';

// Lane IA, two bugs a person found on the dev server (dev 096f506, 2026-10-20),
// on a phone and on a desktop. ① The agent-voice previews overlapped: another
// [들어 보기] did not stop the one playing. One preview player now; "듣는 중…"
// lasts until the sound ends. ② On the experiment tab with an analysed protocol
// picked the state line said "세션 시작을 한 번 눌러주세요" while the console's
// start button was hidden: the tab's card now names the one start that shows,
// and the state line names that button.
// The sound is a stand-in: an Audio class that records play() and pause() in
// place of the browser's (a real preview would need a voice provider).

const PROTOCOL_ID = 'p-ia-bug-1';
const ENTRY = {
  protocol_id: PROTOCOL_ID, title: 'Gamma (예시)', revision_id: 'pdf-1', source_filename: 'gamma.pdf', source_sha256: 'c'.repeat(64),
  available_for_execution: true, analysis_status: 'structured_analysis_ready', lifecycle_state: 'ready', readiness_status: 'guidance_ready',
  step_count: 6, created_at: '2026-10-18T01:00:00+00:00', development_only: false, execution_blocker_codes: [], analysis_outdated_codes: [],
  analysis_outdated_ko: null, review_versions: [], current_review_version: null,
  analysis_run: { state: 'passed', failure_code: null, reanalysis: false, requested_at: '2026-10-18T00:50:30+00:00' },
};
const REVIEW = {
  protocol_id: PROTOCOL_ID, title: 'Gamma (예시)', revision_id: 'pdf-1', lifecycle_state: 'ready', readiness_status: 'guidance_ready',
  available_for_execution: true, analysis_available: true, source: { filename: 'gamma.pdf', sha256: 'a'.repeat(64), page_count: 3 },
  readiness: { status: 'guidance_ready', label: '안내 준비 완료', reasons: [] }, analysis_failure: null, constructs: [],
  execution_blockers: [], execution_notices: [], step_count: 6, sections: [], timers: [],
  safety_notices: [{ step_label: '2', step_id: 's2', source_page_number: 1, source_text: 'Wear gloves.', primary_text: '장갑을 끼세요.' }],
  safety_scope: { usage_scope: 'source_protocol_only', source_protocol_only: true, notice: '기관 안전 문서 없음 · 원문 안전 주의만' },
  pipeline: { stage: 'ready', stage_ko: '실행 가능', blocked: false, message: '분석 통과' },
};
const VOICES = {
  provider: 'google_cloud', default_voice_id: 'ko-KR-Chirp3-HD-Charon', selected: 'default', preview_text_ko: '안녕하세요',
  voices: [{ voice_id: 'ko-KR-Chirp3-HD-Charon', label_ko: '차론 · 남성' }, { voice_id: 'ko-KR-Chirp3-HD-Kore', label_ko: '코레 · 여성' }],
};
// A check with nothing to ask: the sheet goes straight to the safety notice.
const CHECK = { enabled: true, protocol_id: PROTOCOL_ID, target: { kind: 'analysis' }, skipped_reason: 'no_numeric_values', questions: [] };

type FakeAudioRecord = { src: string; paused: boolean; played: number; pauses: number };
declare global {
  interface Window { __audio: FakeAudioRecord[]; __revoked: string[] }
  // The page's own top-level var (index.html).
  var wizard: { settings: object | null; voices: { voices?: object[] } | null };
}

async function mock(page: Page, { catalog = true } = {}) {
  await page.route('**/api/protocols/*/start-check', (route: Route) => route.fulfill({ json: CHECK }));
  // Lane IA (2026-10-22): since lane SAFE (#115) the server keeps the start and the sheet's [읽었어요] leaves the
  // safety acknowledgement at start-check/safety; this spec's protocol is a stand-in, so that answer is one too.
  await page.route('**/api/protocols/*/start-check/safety', (route: Route) => route.fulfill({ json: { ready: true, reason: null, target: CHECK.target, missing_question_ids: [], safety_acknowledged: true } }));
  await page.route('**/api/protocols/*/review', (route: Route) => route.fulfill({ json: REVIEW }));
  await page.route('**/api/protocols/*/reagent-tables', (route: Route) => route.fulfill({ json: { tables: [] } }));
  await page.route('**/api/protocols/*/analysis/status', (route: Route) => route.fulfill({ json: { state: 'passed', worker_running: false } }));
  await page.route('**/api/protocols', (route: Route) => route.fulfill({ json: { protocols: catalog ? [ENTRY] : [] } }));
  await page.route('**/api/voice/voices', (route: Route) => route.fulfill({ json: VOICES }));
  await page.route('**/api/voice/preview', (route: Route) => route.fulfill({ status: 200, contentType: 'audio/wav', body: Buffer.from('RIFF stand-in') }));
}

async function fakeAudio(page: Page) {
  await page.addInitScript(() => {
    const made: FakeAudioRecord[] = [];
    const revoked: string[] = [];
    class FakeAudio {
      src: string; paused = true; played = 0; pauses = 0; onended: (() => void) | null = null; onerror: (() => void) | null = null;
      constructor(src: string) { this.src = src; made.push(this); }
      play() { this.paused = false; this.played += 1; return Promise.resolve(); }
      pause() { this.paused = true; this.pauses += 1; }
    }
    (window as unknown as { Audio: unknown }).Audio = FakeAudio;
    window.__audio = made;
    window.__revoked = revoked;
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.revokeObjectURL = (url: string) => { revoked.push(url); revoke(url); };
  });
}

// The page has loaded what the sheet's steps are built from (the settings and the voices) -- as a person sees the list.
async function pageReady(page: Page) {
  await page.waitForFunction(() => typeof wizard === 'object' && wizard.settings !== null && (wizard.voices?.voices?.length ?? 0) === 2);
}

async function openAgentStep(page: Page) {
  await page.goto(TAB.experiment);
  await pageReady(page);
  await page.locator('#experiment-start-open').click();
  await expect(page.locator('#wizard-title')).toHaveText('마이크 확인 · 목소리 맞추기');
  await page.locator('#wizard-primary').click();
  await expect(page.locator('#wizard-title')).toHaveText('안내 목소리 고르기');
  const asides = page.locator('#wizard-body .wizard-option-aside');
  await expect(asides).toHaveText(['들어 보기', '들어 보기']);
  return asides;
}

test.describe('Bug ①: one agent-voice preview at a time (lane IA)', () => {
  test('another 들어 보기 stops the first, 듣는 중… lasts until the sound ends, the same button again stops', async ({ page }) => {
    await fakeAudio(page);
    await mock(page);
    const asides = await openAgentStep(page);

    await asides.nth(0).click();
    await expect(asides).toHaveText(['듣는 중…', '들어 보기']);
    await expect.poll(() => page.evaluate(() => window.__audio.map(audio => [audio.played, audio.paused]))).toEqual([[1, false]]);

    await asides.nth(1).click();
    await expect(asides).toHaveText(['들어 보기', '듣는 중…']);
    await expect.poll(() => page.evaluate(() => window.__audio.map(audio => [audio.played, audio.paused]))).toEqual([[1, true], [1, false]]);
    expect(await page.evaluate(() => window.__revoked.length)).toBe(1);

    // The sound ends: the label goes back and the sound's URL is let go.
    await page.evaluate(() => { const last = window.__audio[window.__audio.length - 1] as FakeAudioRecord & { onended?: () => void }; last.onended?.(); });
    await expect(asides).toHaveText(['들어 보기', '들어 보기']);
    expect(await page.evaluate(() => window.__revoked.length)).toBe(2);

    await asides.nth(0).click();
    await expect(asides).toHaveText(['듣는 중…', '들어 보기']);
    await expect.poll(() => page.evaluate(() => window.__audio.length)).toBe(3);
    await asides.nth(0).click();
    await expect(asides).toHaveText(['들어 보기', '들어 보기']);
    expect(await page.evaluate(() => window.__audio.filter(audio => !audio.paused).length)).toBe(0);
  });

  test('picking a voice, moving a step and closing the sheet stop the preview', async ({ page }) => {
    await fakeAudio(page);
    await mock(page);
    await page.route('**/api/experimenter/settings', (route: Route) => {
      if (route.request().method() !== 'PUT') return route.fallback();
      return route.fulfill({ json: { settings: { agent_voice: route.request().postDataJSON().agent_voice, voice_calibration: 'none' } } });
    });
    const asides = await openAgentStep(page);
    await asides.nth(0).click();
    await expect(asides).toHaveText(['듣는 중…', '들어 보기']);
    await expect.poll(() => page.evaluate(() => window.__audio.length)).toBe(1);
    await page.locator('#wizard-body .wizard-option-pick').nth(1).click();
    await expect(asides).toHaveText(['들어 보기', '들어 보기']);
    expect(await page.evaluate(() => window.__audio.filter(audio => !audio.paused).length)).toBe(0);

    await asides.nth(1).click();
    await expect(asides).toHaveText(['들어 보기', '듣는 중…']);
    await expect.poll(() => page.evaluate(() => window.__audio.length)).toBe(2);
    await page.locator('#wizard-back').click();
    await expect(page.locator('#wizard-title')).toHaveText('마이크 확인 · 목소리 맞추기');
    expect(await page.evaluate(() => window.__audio.filter(audio => !audio.paused).length)).toBe(0);

    await page.locator('#wizard-primary').click();
    await asides.nth(0).click();
    await expect(asides).toHaveText(['듣는 중…', '들어 보기']);
    await expect.poll(() => page.evaluate(() => window.__audio.length)).toBe(3);
    await page.locator('#wizard-close').click();
    await expect(page.locator('#start-wizard')).toBeHidden();
    expect(await page.evaluate(() => window.__audio.filter(audio => !audio.paused).length)).toBe(0);
    expect(await page.evaluate(() => window.__audio.length)).toBe(3);
  });
});

test.describe('Bug ②: the experiment tab always shows one start (lane IA)', () => {
  test('a picked protocol gets [이 프로토콜로 시작], the state line names it, and the press opens the sheet at its questions', async ({ page }) => {
    await mock(page);
    await page.goto(protocolRoute(PROTOCOL_ID));
    await pageReady(page);
    await expect(page.locator('#protocol-detail-title')).toHaveText('Gamma (예시)');
    await expect(page.locator('#protocol-start')).toBeVisible();

    await page.locator('#tab-link-experiment').click();
    await expect(page.locator('#tab-experiment')).toBeVisible();
    const card = page.locator('#experiment-start-open');
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute('data-start', 'protocol');
    await expect(page.locator('#experiment-start-title')).toHaveText('이 프로토콜로 시작');
    await expect(page.locator('#experiment-start-sub')).toContainText('Gamma (예시)');
    await expect(page.locator('#status')).toHaveText("준비됨 · '이 프로토콜로 시작' 단추를 눌러 주세요.");
    // The console's own start button waits for the 실험 후 view: nothing on the tab points at it.
    await expect(page.locator('#start')).toBeHidden();
    await expect(page.locator('#tab-experiment')).not.toContainText('세션 시작을 한 번');

    await card.click();
    await expect(page.locator('#start-wizard')).toBeVisible();
    await expect(page.locator('#wizard-title')).toHaveText('안전 주의');
    await expect(page.locator('#wizard-body')).toContainText('장갑을 끼세요.');
    await page.locator('#wizard-primary').click();
    await expect(page.locator('#wizard-primary')).toHaveText('이 프로토콜로 시작');
    await page.locator('#wizard-close').click();
    await expect(page.locator('#start-wizard')).toBeHidden();
    // The safety notice was read in this sheet: the card says the next press starts at once.
    await expect(page.locator('#experiment-start-sub')).toContainText('바로 시작');
    await expect(page.locator('#status')).toHaveText("준비됨 · '이 프로토콜로 시작' 단추를 눌러 주세요.");
  });

  test('with no protocol picked the card is [새 실험 시작] and the state line names it', async ({ page }) => {
    await mock(page, { catalog: false });
    await page.goto(TAB.experiment);
    await pageReady(page);
    const card = page.locator('#experiment-start-open');
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute('data-start', 'new');
    await expect(page.locator('#experiment-start-title')).toHaveText('새 실험 시작');
    await expect(page.locator('#status')).toHaveText("준비됨 · '새 실험 시작' 단추를 눌러 주세요.");
    await expect(page.locator('#start')).toBeHidden();
    await card.click();
    await expect(page.locator('#start-wizard')).toBeVisible();
    await expect(page.locator('#wizard-title')).toHaveText('마이크 확인 · 목소리 맞추기');
  });
});
