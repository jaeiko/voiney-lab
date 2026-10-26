import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { Page, TestInfo } from '@playwright/test';
import { expect } from '@playwright/test';

import { APP, GREETING, inView, notClipped, overflow, overflowing, StandIn, STEPS } from './app-rx2';

// Lane RX2 (2026-10-25): the new screen's walk, on every project (desktop, phone, tablet portrait and
// landscape): login -> the first guide (microphone, voice check skipped, a guiding voice) -> home -> the
// start flow one screen at a time (the protocol, the key values, the safety notices, today's values, the
// start with the noise choice) -> the experiment screen (the step, a timer, a memo and a photo, a picture
// full screen, a word the server dropped, a pause and its resume, the next step with a timer in its last
// minute) -> [종료] -> the end. On the experiment screen the records and their two buttons are seen without
// scrolling and the caption shows its whole sentence. With RX2_SHOTS set, each screen is photographed there.

export async function shot(page: Page, info: TestInfo, name: string) {
  const dir = process.env.RX2_SHOTS;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  const project = info.project.name.toLowerCase().replace(/\s+/g, '-');
  await page.screenshot({ path: join(dir, `${project}-${name}.png`), fullPage: false });
}

async function noOverflow(page: Page, where: string) {
  const width = await overflow(page);
  expect(width, `horizontal overflow on ${where}: ${(await overflowing(page)).join('; ')}`).toBeLessThanOrEqual(0);
}

export async function walk(page: Page, info: TestInfo) {
  const server = new StandIn();
  await server.attach(page);
  await page.goto(APP);

  // Login.
  await page.getByLabel('이름').fill('김재준');
  await page.getByLabel('비밀번호').fill('password-1');
  await shot(page, info, '01-login');
  await page.getByRole('button', { name: '로그인하기' }).click();

  // The first guide, once after the login (decision 6).
  await expect(page.getByTestId('welcome-screen')).toBeVisible();
  await expect(page).toHaveURL(/#\/welcome$/);
  await expect(page.getByTestId('welcome-why')).toContainText('마이크가 필요해요');
  await noOverflow(page, 'the first guide');
  await shot(page, info, '02-welcome-mic');
  await page.getByTestId('welcome-mic').click();
  await expect(page.getByTestId('welcome-mic-ok')).toBeVisible();
  await page.getByTestId('welcome-next').click();
  await expect(page.getByTestId('voice-check')).toBeVisible();
  await shot(page, info, '03-welcome-voice');
  // 목소리 맞추기: five seconds read on the fake microphone, posted with its short id, kept by the server.
  await page.getByTestId('voice-check-start').click();
  await expect(page.getByTestId('voice-check-result')).toContainText('목소리를 맞췄어요', { timeout: 20_000 });
  expect(server.calibrations).toHaveLength(1);
  expect(server.calibrations[0].pcm_base64.length).toBeGreaterThan(100_000);
  expect(server.calibrations[0].mic_id).toMatch(/^mic-[0-9a-f]{8}$/);
  await page.getByTestId('welcome-next').click();
  await expect(page.getByTestId('voice-picker')).toBeVisible();
  await page.locator('[data-voice="ko-KR-Chirp3-HD-Kore"]').click();
  await expect(page.getByTestId('voice-saved')).toHaveText('저장했어요');
  expect(server.settingPuts).toContainEqual({ agent_voice: 'ko-KR-Chirp3-HD-Kore' });
  await shot(page, info, '04-welcome-agent');
  await page.getByTestId('welcome-done').click();

  // Home: the start card goes to the start flow.
  await expect(page).toHaveURL(/#\/home$/);
  await expect(page.locator('[data-card="start"]')).toBeVisible();
  await shot(page, info, '05-home');
  await page.locator('[data-card="start"]').click();

  // ① The protocol.
  await expect(page.getByTestId('start-screen')).toBeVisible();
  await expect(page.getByTestId('start-why')).toHaveText('어떤 원문으로 실험할지 확인해요.');
  await expect(page.locator('.step-progress li[aria-current="step"]')).toContainText('프로토콜 확인');
  await page.getByRole('button', { name: /In-gel digestion/ }).click();
  await noOverflow(page, 'start ①');
  await shot(page, info, '06-start-1-protocol');
  await page.getByTestId('start-next').click();

  // ② The key values, with the reason first (decision 1).
  await expect(page.getByTestId('start-why')).toHaveText('원문에 적힌 값을 한 번 확인해요 — 잘못 분석된 값을 막으려고요.');
  await expect(page.getByTestId('check-progress')).toHaveText('2개 중 1번째');
  await shot(page, info, '07-start-2-check');
  await page.getByRole('button', { name: '10 min', exact: true }).click();
  await expect(page.getByTestId('check-progress')).toHaveText('2개 중 2번째');
  // Back once and forward again: the way back is there.
  await page.getByTestId('start-back').click();
  await expect(page.locator('.step-progress li[aria-current="step"]')).toContainText('프로토콜 확인');
  await page.getByTestId('start-next').click();
  await page.getByRole('button', { name: '10 min', exact: true }).click();
  await page.getByRole('button', { name: '56 °C', exact: true }).click();
  expect(server.answers.length).toBe(3);

  // ③ The safety notices.
  await expect(page.getByTestId('start-why')).toContainText('안전 주의를 읽어요');
  await expect(page.getByTestId('safety-list').locator('li')).toHaveCount(2);
  await noOverflow(page, 'start ③');
  await shot(page, info, '08-start-3-safety');
  await page.getByTestId('start-next').click();
  expect(server.safetyReads).toBe(1);

  // ④ Today's values (lane RX1's piece): none changed.
  await expect(page.getByTestId('start-why')).toContainText('오늘 원문과 다르게 할 값');
  await expect(page.getByTestId('changes-none')).toBeVisible();
  await shot(page, info, '09-start-4-changes');
  await page.getByTestId('changes-none').click();
  expect(server.changeChoices).toEqual([{ target: expect.any(Object), changes: [], source: 'wizard' }]);

  // ⑤ The start: the noise choice, its one line, the fixed start button.
  await expect(page.getByTestId('ambient-choice')).toContainText('시끄러우면 기록·단계 이동 전에 한 번 더 확인해요.');
  await expect(page.getByTestId('start-go')).toHaveText('이 프로토콜로 시작');
  await shot(page, info, '10-start-5-go');
  await page.getByText('시끄러워요', { exact: true }).click();
  // The shared voice code is already on the page, so the press itself primes the sound (voice-core's primeRunStart).
  await page.waitForFunction(() => typeof (window as any).primeRunStart === 'function');
  await page.evaluate(() => { const w = window as any; const original = w.primeRunStart; w.__primed = 0; w.primeRunStart = function (...args: unknown[]) { w.__primed += 1; return original.apply(this, args); }; });
  await page.getByTestId('start-go').click();
  expect(await page.evaluate(() => (window as any).__primed)).toBe(1);
  await expect.poll(() => server.settingPuts).toContainEqual({ ambient_mode: 'noisy' });

  // The experiment screen.
  await expect(page).toHaveURL(/#\/experiment$/);
  // Lane HEAR decision 2: the run tells the server its microphone's id.
  await expect.poll(() => server.audioReady.length).toBe(1);
  expect(server.audioReady[0].mic_id).toBe(server.calibrations[0].mic_id);
  await expect(page.getByTestId('run-step-text')).toHaveText(STEPS[0].korean!);
  expect(server.starts[0]).toMatchObject({ protocol_id: 'p-rx2-1', run_change_set_id: 'run-changes-0123456789abcdef01234567', mode: 'cascade' });
  await expect(page.getByTestId('run-listening')).toBeVisible();
  // No run bar outside the run: the app's menu is not shown while it runs.
  await expect(page.locator('nav.nav')).toHaveCount(0);
  // The timer is a small chip while far from its end.
  await expect(page.getByTestId('run-timer')).toHaveAttribute('data-big', 'false');
  // The caption: whole sentences in at most two lines, nothing cut -- a long greeting in pages, as subtitles.
  const captionText = page.getByTestId('run-caption-text');
  await expect(captionText).toHaveText(/^실험 목적/);
  const first = await notClipped(page, '[data-testid="run-caption-text"]');
  expect(first.ok, `caption clipped: ${JSON.stringify(first)}`).toBe(true);
  expect(GREETING.startsWith((await captionText.textContent()) ?? '-')).toBe(true);
  await expect(captionText).toHaveText(/단계마다 말로 안내할게요\.$/, { timeout: 20_000 });
  const last = await notClipped(page, '[data-testid="run-caption-text"]');
  expect(last.ok, `caption clipped: ${JSON.stringify(last)}`).toBe(true);
  // Person's decision 2 (2026-10-26; checked after the caption, whose first page would otherwise have passed): the Korean large and bold, the source small under it, folded; opened, it reads at
  // less than two thirds of the Korean's size, and the step's label is not repeated in the sentence (decision 5).
  await expect(page.getByTestId('run-step-text')).toHaveAttribute('data-lang', 'ko');
  await expect(page.getByTestId('run-source')).not.toHaveAttribute('open');
  await page.getByTestId('run-source').locator('summary').click();
  await expect(page.getByTestId('run-source-text')).toHaveText(STEPS[0].source);
  const sizes = await page.evaluate(() => ['run-step-text', 'run-source-text'].map((id) => parseFloat(getComputedStyle(document.querySelector(`[data-testid="${id}"]`)!).fontSize)));
  expect(sizes[1], `source ${sizes[1]}px beside Korean ${sizes[0]}px`).toBeLessThan(sizes[0] * 0.67);
  await page.getByTestId('run-source').locator('summary').click();
  // The next step keeps its one label as the server writes it, under the line's own name (lane KO2, decision 5).
  await expect(page.getByTestId('run-next').locator('.run-next-label')).toHaveText('다음 단계');
  await expect(page.getByTestId('run-next-text')).toHaveText(`${STEPS[1].label}단계: ${STEPS[1].korean}`);
  // The records and their two buttons, without scrolling.
  expect(await inView(page, '[data-testid="run-records"] .run-records-head')).toBe(true);
  expect(await inView(page, '[data-testid="record-memo"]')).toBe(true);
  expect(await inView(page, '[data-testid="record-photo"]')).toBe(true);
  await expect(page.getByTestId('run-records')).toContainText('젤 조각이 투명해졌어요');
  // A narrow screen folds the list under its head (the latest record shown); it opens on a press.
  if (await page.getByTestId('records-toggle').isVisible()) {
    await expect(page.getByTestId('records-latest')).toContainText('말로 기록');
    await page.getByTestId('records-toggle').click();
    await expect(page.getByTestId('records-list')).toBeVisible();
    expect(await inView(page, '[data-testid="record-memo"]')).toBe(true);
    await shot(page, info, '11b-run-records-open');
    await page.getByTestId('records-toggle').click();
  }
  await noOverflow(page, 'the experiment screen');
  await shot(page, info, '11-run');

  // A memo.
  await page.getByTestId('record-memo').click();
  await page.getByTestId('memo-input').fill('젤 조각 4개 확인');
  await shot(page, info, '12-run-memo');
  await page.getByTestId('memo-save').click();
  await expect(page.getByTestId('run-records')).toContainText('젤 조각 4개 확인');
  expect(server.memos).toEqual(['젤 조각 4개 확인']);
  // A photo.
  await page.getByTestId('record-photo-input').setInputFiles({ name: 'gel-pieces.png', mimeType: 'image/png', buffer: Buffer.from([137, 80, 78, 71, 13, 10, 26, 10, 0]) });
  await expect(page.getByTestId('run-records')).toContainText('gel-pieces.png');
  expect(server.photos).toEqual(['gel-pieces.png']);

  // The picture, large, and across the whole screen on a press.
  await expect(page.getByTestId('run-figures')).toBeVisible();
  await page.getByTestId('figure-open').click();
  await expect(page.getByTestId('figure-full')).toBeVisible();
  await shot(page, info, '13-run-figure-full');
  await page.getByTestId('figure-close').click();
  await expect(page.getByTestId('figure-full')).toHaveCount(0);

  // A word the server dropped (speech.dropped) is said: 잘 안 들렸어요.
  server.misheard();
  await expect(page.getByTestId('run-listening')).toHaveAttribute('data-word', 'misheard');
  await expect(page.getByTestId('run-listening')).toContainText('잘 안 들렸어요');
  await shot(page, info, '14-run-misheard');

  // The pause: a band across the whole screen, the listening word 일시정지; resumed from the band.
  await page.evaluate(() => { const w = window as any; const original = w.clearPlayback; w.__cleared = 0; w.clearPlayback = function (...args: unknown[]) { w.__cleared += 1; return original.apply(this, args); }; });
  await page.getByTestId('run-pause').click();
  await expect(page.getByTestId('run-paused')).toBeVisible();
  await expect(page.getByTestId('run-paused')).toContainText("일시정지 중 — '다시 시작'이라고 말하거나 [다시 시작]을 누르세요");
  await expect(page.getByTestId('run-listening')).toHaveAttribute('data-word', 'paused');
  expect(server.received).toContain('workflow.pause');
  // playback.stop came with the pause and stopped the guidance (voice-core's clearPlayback).
  await expect.poll(() => page.evaluate(() => (window as any).__cleared)).toBeGreaterThanOrEqual(1);
  await shot(page, info, '15-run-paused');
  // The band offers [종료] too (person's decision 1 of 2026-10-26), enabled.
  await expect(page.getByTestId('run-paused-stop')).toBeEnabled();
  await page.getByTestId('run-resume').click();
  await expect(page.getByTestId('run-paused')).toHaveCount(0);
  expect(server.received).toContain('workflow.resume');

  // Lane RX4 (the person's test of 2026-10-26): a joined utterance or a barge-in moved the server's generation on; the
  // server now fences a resume by its configuration alone (decision 3), and the screen sends the newest generation it saw
  // anyway, so [다시 시작] still works: pause -> resume -> pause -> resume.
  const before = server.generation;
  server.supersede();
  await page.waitForFunction((wanted) => [...(window as any).turnServerGenerations.values()].includes(wanted), before + 1);
  await page.getByTestId('run-pause').click();
  await expect(page.getByTestId('run-paused')).toBeVisible();
  await page.getByTestId('run-resume').click();
  await expect(page.getByTestId('run-paused')).toHaveCount(0);
  expect(server.lastControl('workflow.resume')?.generation).toBe(server.generation);
  expect(server.paused).toBe(false);

  // The next step: its timer in its last minute is large.
  server.nextStep();
  await expect(page.getByTestId('run-step-text')).toHaveText(STEPS[1].korean);
  await expect(page.getByTestId('run-timer')).toHaveAttribute('data-big', 'true');
  expect(await inView(page, '[data-testid="record-memo"]')).toBe(true);
  await shot(page, info, '16-run-timer-last-minute');

  // The conversation drawer from the caption.
  await page.getByTestId('run-caption').click();
  await expect(page.getByTestId('run-drawer')).toContainText(GREETING);
  await shot(page, info, '17-run-drawer');
  await page.keyboard.press('Escape');

  // A lost connection: connection.js (shared, unchanged) sees it and says it, the step stays, and the same experiment goes on.
  await server.drop();
  await expect(page.locator('#connection-band')).toContainText('연결이 끊겼어요');
  await expect(page.getByTestId('run-listening')).toHaveAttribute('data-word', 'reconnecting');
  await expect(page.getByTestId('run-step-text')).toHaveText(STEPS[1].korean);
  await shot(page, info, '19-run-connection-lost');
  await expect(page.locator('#connection-band')).toContainText('다시 연결됐어요', { timeout: 20_000 });
  expect(server.starts.length).toBe(2);
  expect(server.starts[1]).toMatchObject({ experiment_session_id: 's-rx2-1', reconnect: expect.any(Object) });
  await expect(page.getByTestId('run-listening')).not.toHaveAttribute('data-word', 'reconnecting');
  await expect(page.locator('#connection-band')).toBeHidden({ timeout: 10_000 });

  // [종료] from the paused band (decision 1: 일시정지 → 다시 시작 → 일시정지 → 종료·저장): asked once, then the end.
  await page.getByTestId('run-pause').click();
  await expect(page.getByTestId('run-paused')).toBeVisible();
  await page.getByTestId('run-paused-stop').click();
  await page.getByTestId('run-stop-confirm').click();
  await expect(page.getByTestId('run-ended')).toContainText('실험을 멈췄어요');
  expect(server.received).toContain('session.stop');
  // The end screen (decision 3): what was done, three numbers from the run, one question, chips, a line, [보내기].
  await expect(page.getByTestId('end-sub')).toContainText('In-gel digestion');
  await expect(page.getByTestId('end-counts').locator('[data-count="voice"] .end-count-number')).toHaveText('1');
  await expect(page.getByTestId('end-counts').locator('[data-count="changes"] .end-count-number')).toHaveText('0');
  await expect(page.getByTestId('end-counts').locator('[data-count="help"] .end-count-number')).toHaveText('0');
  await expect(page.getByTestId('end-send')).toBeDisabled();
  await noOverflow(page, 'the end');
  await shot(page, info, '18-run-ended');
  await page.getByRole('radio', { name: '편했어요', exact: true }).click();
  await page.getByRole('button', { name: '말을 잘 알아들었어요' }).click();
  await page.getByRole('button', { name: '타이머가 편했어요' }).click();
  await page.getByTestId('end-note').fill('소리가 조금 작았어요');
  await shot(page, info, '18b-run-ended-answered');
  await page.getByTestId('end-send').click();
  await expect(page.getByTestId('end-thanks')).toContainText('고마워요');
  expect(server.feedback).toEqual([{ report_id: server.report, rating: 4, skipped: false, note: '소리가 조금 작았어요', tags: ['heard_well', 'timer_easy'] }]);
  await expect(page.getByTestId('end-home')).toHaveAttribute('href', '#/home');
  await shot(page, info, '18c-run-ended-sent');
}

/** The settings screen, photographed and checked on its own. */
export async function settingsWalk(page: Page, info: TestInfo) {
  const server = new StandIn({ signedIn: true });
  await server.attach(page);
  // Straight to the settings (the first guide is asked only on the home); no init script, which a worklet's scope would run too.
  await page.goto(`${APP}#/settings`);
  await expect(page.getByTestId('settings-screen')).toBeVisible();
  await expect(page.getByTestId('settings-filter')).toContainText('목소리를 맞춘 뒤에 켤 수 있어요.');
  await expect(page.getByTestId('filter-switch').locator('input[value="on"]')).toBeDisabled();
  // Measured here, the filter can be turned on: speaker_filter on.
  await page.getByTestId('voice-check-start').click();
  await expect(page.getByTestId('voice-check-result')).toContainText('목소리를 맞췄어요', { timeout: 20_000 });
  await expect(page.getByTestId('filter-switch').locator('input[value="on"]')).toBeEnabled();
  await page.getByTestId('filter-switch').getByText('켜기').click();
  await expect(page.getByTestId('settings-filter')).toContainText('저장했어요');
  expect(server.settingPuts).toContainEqual({ speaker_filter: 'on' });
  await page.getByText('먼저 물어보기', { exact: true }).click();
  await expect(page.getByTestId('settings-numbers')).toContainText('저장했어요');
  expect(server.settingPuts).toContainEqual({ confirm_mode: 'confirm' });
  await noOverflow(page, 'the settings');
  await shot(page, info, '20-settings');
}

