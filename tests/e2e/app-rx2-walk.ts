import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import type { Page, TestInfo } from '@playwright/test';
import { expect } from '@playwright/test';

import { APP, GREETING, inView, notClipped, overflow, StandIn, STEPS } from './app-rx2';

// Lane RX2 (2026-10-25): the new screen's walk, on every project (desktop, phone, tablet portrait and
// landscape): login -> the first guide (microphone, voice check skipped, a guiding voice) -> home -> the
// start flow one screen at a time (the protocol, the key values, the safety notices, today's values, the
// start with the noise choice) -> the experiment screen (the step, a timer, a memo and a photo, a picture
// full screen, a word the server dropped, a pause and its resume, the next step with a timer in its last
// minute) -> [종료] -> the end. On the experiment screen the records and their two buttons are seen without
// scrolling and the caption shows its whole sentence. With RX2_SHOTS set, each screen is photographed there.

async function shot(page: Page, info: TestInfo, name: string) {
  const dir = process.env.RX2_SHOTS;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  const project = info.project.name.toLowerCase().replace(/\s+/g, '-');
  await page.screenshot({ path: join(dir, `${project}-${name}.png`), fullPage: false });
}

async function noOverflow(page: Page, where: string) {
  expect(await overflow(page), `horizontal overflow on ${where}`).toBeLessThanOrEqual(0);
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
  await page.getByTestId('voice-check-skip').click();
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

  // ④ Today's values: none changed.
  await expect(page.getByTestId('changes-list')).toBeVisible();
  await shot(page, info, '09-start-4-changes');
  await page.getByTestId('start-next').click();
  expect(server.changeChoices).toEqual([{ target: expect.any(Object), changes: [], source: 'wizard' }]);

  // ⑤ The start: the noise choice, its one line, the fixed start button.
  await expect(page.getByTestId('ambient-choice')).toContainText('시끄러우면 기록·단계 이동 전에 한 번 더 확인해요.');
  await expect(page.getByTestId('start-go')).toHaveText('이 프로토콜로 시작');
  await shot(page, info, '10-start-5-go');
  await page.getByText('시끄러워요', { exact: true }).click();
  await page.getByTestId('start-go').click();
  await expect.poll(() => server.settingPuts).toContainEqual({ ambient_mode: 'noisy' });

  // The experiment screen.
  await expect(page).toHaveURL(/#\/experiment$/);
  await expect(page.getByTestId('run-step-text')).toHaveText(STEPS[0].korean);
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

  // A word the server dropped is said: 잘 안 들렸어요.
  server.misheard();
  await expect(page.getByTestId('run-listening')).toHaveAttribute('data-word', 'misheard');
  await expect(page.getByTestId('run-listening')).toContainText('잘 안 들렸어요');
  await shot(page, info, '14-run-misheard');

  // The pause: a band across the whole screen, the listening word 일시정지; resumed from the band.
  await page.getByTestId('run-pause').click();
  await expect(page.getByTestId('run-paused')).toBeVisible();
  await expect(page.getByTestId('run-paused')).toContainText("일시정지 중 — '다시 시작'이라고 말하거나 [다시 시작]을 누르세요");
  await expect(page.getByTestId('run-listening')).toHaveAttribute('data-word', 'paused');
  expect(server.received).toContain('workflow.pause');
  await shot(page, info, '15-run-paused');
  await page.getByTestId('run-resume').click();
  await expect(page.getByTestId('run-paused')).toHaveCount(0);
  expect(server.received).toContain('workflow.resume');

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

  // [종료]: asked once, then the end.
  await page.getByTestId('run-stop').click();
  await page.getByTestId('run-stop-confirm').click();
  await expect(page.getByTestId('run-ended')).toContainText('실험을 멈췄어요');
  expect(server.received).toContain('session.stop');
  await noOverflow(page, 'the end');
  await shot(page, info, '18-run-ended');
}

/** The settings screen, photographed and checked on its own. */
export async function settingsWalk(page: Page, info: TestInfo) {
  const server = new StandIn({ signedIn: true });
  await server.attach(page);
  await page.addInitScript(() => localStorage.setItem('voiney-lab.app-welcome:a-rx2', 'done'));
  await page.goto(`${APP}#/settings`);
  await expect(page.getByTestId('settings-screen')).toBeVisible();
  await expect(page.getByTestId('settings-filter')).toContainText('목소리를 맞춘 뒤에 켤 수 있어요.');
  await expect(page.getByTestId('filter-switch').locator('input')).toBeDisabled();
  await page.getByText('먼저 물어보기', { exact: true }).click();
  await expect(page.getByTestId('settings-numbers')).toContainText('저장했어요');
  expect(server.settingPuts).toContainEqual({ confirm_mode: 'confirm' });
  await noOverflow(page, 'the settings');
  await shot(page, info, '20-settings');
}
