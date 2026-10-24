import { test, expect, type Page, type Route } from '@playwright/test';

declare global {
  // The page's own top-level var (index.html).
  var wizard: { settings: object | null; voices: { voices?: object[] } | null };
}

// Lane ON stage ② (human decision 2026-10-16/18): the start, one thing per
// screen -- the voice, the agent's voice, the protocol, two key values, the
// safety check, the start. Lane IA (2026-10-19): the steps open as a sheet
// over the page from the experiment tab's start button (and the home's
// card); the view behind "자세히 보기" is gone.
// Server answers are mocked with page.route; nothing talks to a model. The
// CI server runs in dev mode (no login step), so the wizard opens on the
// microphone step.

const CATALOG = {
  protocols: [{
    protocol_id: 'p-on-1', title: 'Gamma (예시)', revision_id: 'pdf-1', source_filename: 'gamma.pdf', source_sha256: 'c'.repeat(64),
    available_for_execution: true, analysis_status: 'structured_analysis_ready', lifecycle_state: 'ready', readiness_status: 'guidance_ready',
    step_count: 6, created_at: '2026-10-18T01:00:00+00:00', development_only: false, execution_blocker_codes: [], analysis_outdated_codes: [],
    analysis_outdated_ko: null, review_versions: [], current_review_version: null,
    analysis_run: { state: 'passed', failure_code: null, reanalysis: false, requested_at: '2026-10-18T00:50:30+00:00' },
  }],
};
const REVIEW = {
  protocol_id: 'p-on-1', title: 'Gamma (예시)', revision_id: 'pdf-1', lifecycle_state: 'ready', readiness_status: 'guidance_ready',
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
const question = (position: number, id: string, kind: string, kindKo: string, words: string, choices: string[]) => ({
  question_id: id, position, total: 2, progress_ko: `${position} / 2`, kind, kind_ko: kindKo, step_label: '2', item_id: 'step:s2',
  question_ko: words, source: { page: 1, excerpt: '2. Incubate for 10 min at 37 C.', lines: { page: 1, first_line: 4, last_line: 4 } },
  choices: choices.map((label, index) => ({ choice_id: `c${index + 1}`, label_ko: label })),
});
const CHECK = {
  enabled: true, protocol_id: 'p-on-1', target: { kind: 'analysis' }, skipped_reason: null,
  questions: [
    question(1, 'q1:step:s2:10min', 'time', '시간', '2단계의 시간은(는) 원문에 얼마로 적혀 있나요?', ['100 min', '10 min', '5 min']),
    question(2, 'q2:step:s2:37°c', 'temperature', '온도', '2단계의 온도은(는) 원문에 얼마로 적혀 있나요?', ['37 C', '370 C', '4 C']),
  ],
};
const answer = (body: { choice_id?: string; action?: string; question_id: string }) => {
  const right = (body.question_id.startsWith('q1') && body.choice_id === 'c2') || (body.question_id.startsWith('q2') && body.choice_id === 'c1');
  if (body.action === 'accept' || right) return { question_id: body.question_id, correct: body.action ? null : true, proceed: true, chosen_label: '', next_question_id: body.question_id.startsWith('q1') ? 'q2:step:s2:37°c' : null, message_ko: '', followups: [], card: null };
  return {
    question_id: body.question_id, correct: false, proceed: false, chosen_label: '', next_question_id: null, message_ko: '원문 조각을 다시 봐 주세요.',
    followups: [{ choice_id: 'accept', label_ko: '원문대로 이 값이 맞아요', request: { question_id: body.question_id, action: 'accept' } }, { choice_id: 'open_card', label_ko: '분석 값이 틀렸어요 → 검토 카드 열기', request: null }],
    card: null,
  };
};

async function mock(page: Page) {
  const answers: object[] = [];
  await page.route('**/api/protocols/p-on-1/start-check/answers', async (route: Route) => {
    const body = route.request().postDataJSON();
    answers.push(body);
    await route.fulfill({ json: answer(body) });
  });
  await page.route('**/api/protocols/p-on-1/start-check', (route: Route) => route.fulfill({ json: CHECK }));
  // Lane IA (2026-10-22): since lane SAFE (#115) the server keeps the start and the sheet's [읽었어요] leaves the
  // safety acknowledgement at start-check/safety; this spec's protocol is a stand-in, so that answer is one too.
  await page.route('**/api/protocols/p-on-1/start-check/safety', (route: Route) => route.fulfill({ json: { ready: true, reason: null, target: CHECK.target, missing_question_ids: [], safety_acknowledged: true } }));
  // Lane EASY decision 2 (2026-10-24): "오늘 원문과 다르게 할 값" comes after the safety check; this stand-in offers none and keeps the skip.
  await page.route('**/api/protocols/p-on-1/start-check/changes', (route: Route) => route.fulfill({ json: route.request().method() === 'POST' ? { change_set_id: 'run-changes-0123456789abcdef01234567', source: 'wizard', changes: [] } : { protocol_id: 'p-on-1', target: CHECK.target, revision_id: 'pdf-1', limit: 12, values: [] } }));
  await page.route('**/api/protocols/p-on-1/review', (route: Route) => route.fulfill({ json: REVIEW }));
  await page.route('**/api/protocols/p-on-1/reagent-tables', (route: Route) => route.fulfill({ json: { tables: [] } }));
  await page.route('**/api/protocols/p-on-1/analysis/status', (route: Route) => route.fulfill({ json: { state: 'passed', worker_running: false } }));
  await page.route('**/api/protocols', (route: Route) => route.fulfill({ json: CATALOG }));
  await page.route('**/api/voice/voices', (route: Route) => route.fulfill({ json: VOICES }));
  return answers;
}

// Lane IA (2026-10-22, handed over by lane SAFE: CI flaked at 5733fe1 on '← 이전'): the sheet's steps are built
// from the settings and the voices the page loads at start; on a cold server the first press could come
// before the voices, so the agent's step was not yet there. Wait for them, as a person sees the list.
async function pageReady(page: Page) {
  await page.waitForFunction(() => typeof wizard === 'object' && wizard.settings !== null && (wizard.voices?.voices?.length ?? 0) === 2);
}

test.describe('Start wizard (lane ON)', () => {
  test('opens one step at a time and walks to the start through the servers questions', async ({ page }) => {
    const answers = await mock(page);
    await page.goto('/#/experiment');
    await pageReady(page);
    // Lane IA addendum: the steps open as a sheet from the experiment tab's start button.
    await page.locator('#experiment-start-open').click();
    await expect(page.locator('#start-wizard')).toBeVisible();
    await expect(page.locator('body')).toHaveAttribute('data-wizard', 'on');
    await expect(page.locator('#start')).toBeHidden();
    await expect(page.locator('#wizard-title')).toHaveText('마이크 확인 · 목소리 맞추기');
    // Lane EASY decision 2: one more step ("오늘 원문과 다르게 할 값").
    await expect(page.locator('#wizard-dots li')).toHaveCount(7);
    await expect(page.locator('#wizard-back')).toBeHidden();
    const primary = page.locator('#wizard-primary');
    const box = await primary.boundingBox();
    expect(box && box.height).toBeGreaterThanOrEqual(64);
    await primary.click();
    await expect(page.locator('#wizard-title')).toHaveText('안내 목소리 고르기');
    await expect(page.locator('#wizard-body .wizard-option-pick')).toHaveCount(2);
    await expect(page.locator('#wizard-back')).toBeVisible();
    await primary.click();
    await expect(page.locator('#wizard-title')).toHaveText('프로토콜 고르기');
    await expect(primary).toBeDisabled();
    await page.locator('#wizard-body .wizard-option-pick').first().click();
    await expect(primary).toBeEnabled();
    await primary.click();
    await expect(page.locator('#wizard-title')).toHaveText('2단계의 시간은(는) 원문에 얼마로 적혀 있나요?');
    await expect(page.locator('#wizard-body .wizard-excerpt')).toContainText('Incubate for 10 min');
    const choices = page.locator('#wizard-body .choice-card-choice');
    await expect(choices).toHaveText(['100 min', '10 min', '5 min']);
    await choices.nth(0).click();
    await expect(page.locator('#wizard-body')).toContainText('원문 조각을 다시 봐 주세요');
    await expect(page.locator('#wizard-body .choice-card-choice')).toHaveText(['원문대로 이 값이 맞아요', '분석 값이 틀렸어요 → 검토 카드 열기']);
    await page.locator('#wizard-body .choice-card-choice').first().click();
    await expect(page.locator('#wizard-title')).toContainText('온도');
    await page.locator('#wizard-body .choice-card-choice', { hasText: '37 C' }).click();
    await expect(page.locator('#wizard-title')).toHaveText('안전 주의');
    await expect(page.locator('#wizard-body')).toContainText('장갑을 끼세요.');
    await expect(page.locator('#wizard-body')).toContainText('기관 안전 문서 없음 · 원문 안전 주의만');
    await primary.click();
    // Lane EASY decision 2: nothing to change today, so the step is passed with its one button.
    await expect(page.locator('#wizard-title')).toHaveText('오늘 원문과 다르게 할 값');
    await primary.click();
    await expect(page.locator('#wizard-title')).toHaveText('Gamma (예시)');
    await expect(primary).toHaveText('이 프로토콜로 시작');
    expect(answers.length).toBe(3);
    expect((answers[1] as { action?: string }).action).toBe('accept');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test('← 이전 goes back one step', async ({ page }) => {
    await mock(page);
    await page.goto('/#/experiment');
    await pageReady(page);
    await page.locator('#experiment-start-open').click();
    await page.locator('#wizard-primary').click();
    await expect(page.locator('#wizard-title')).toHaveText('안내 목소리 고르기');
    await page.locator('#wizard-back').click();
    await expect(page.locator('#wizard-title')).toHaveText('마이크 확인 · 목소리 맞추기');
  });
});
