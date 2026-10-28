import type { Page } from '@playwright/test';

import { expect } from './routes';
import { APP, PROTOCOLS, StandIn, overflow } from './app-rx2';

/** HOOD1: from the final start press onward, every ordinary run action is spoken. */
export async function hood1Walk(page: Page, protocol: keyof typeof PROTOCOLS = 'ingel') {
  const server = new StandIn({ signedIn: true, protocol });
  const first = server.protocol.steps[0];
  const second = server.protocol.steps[1];
  await server.attach(page);
  await page.goto(`${APP}#/start/${server.protocol.id}?step=5`);
  await expect(page.getByTestId('start-go')).toHaveText('이 프로토콜로 시작');
  await page.getByTestId('start-go').click();
  await expect(page.getByTestId('run-step-text')).toHaveText(first.korean ?? first.source);

  await page.evaluate(() => {
    (window as any).__hood1Touches = 0;
    for (const name of ['pointerdown', 'touchstart', 'click']) {
      window.addEventListener(name, () => { (window as any).__hood1Touches += 1; }, { capture: true });
    }
  });

  server.voice('화면 크게');
  await expect(page.getByTestId('run-screen')).toHaveAttribute('data-display', 'far');
  await expect(page.getByTestId('run-far-step')).toHaveText(first.korean ?? '번역 준비 중');
  if (!first.korean) await expect(page.getByTestId('run-far-step')).not.toContainText(first.source);
  const far = await page.evaluate(() => ({
    step: parseFloat(getComputedStyle(document.querySelector('[data-testid="run-far-step"]')!).fontSize),
    timer: document.querySelector('[data-testid="run-far-timer"] .run-timer-digits')
      ? parseFloat(getComputedStyle(document.querySelector('[data-testid="run-far-timer"] .run-timer-digits')!).fontSize)
      : null,
    prompt: parseFloat(getComputedStyle(document.querySelector('[data-testid="run-far-prompt"]')!).fontSize),
    background: getComputedStyle(document.querySelector('[data-testid="run-screen"]')!).backgroundColor,
    color: getComputedStyle(document.querySelector('[data-testid="run-far-step"]')!).color,
  }));
  expect(far.step).toBeGreaterThanOrEqual(72);
  if (first.timerSeconds) expect(far.timer).toBeGreaterThanOrEqual(144);
  else expect(far.timer).toBeNull();
  expect(far.prompt).toBeGreaterThanOrEqual(32);
  expect(far.background).toBe('rgb(28, 28, 26)');
  expect(far.color).toBe('rgb(255, 255, 255)');
  expect(await overflow(page)).toBe(0);

  server.voice('보통 화면');
  await expect(page.getByTestId('run-screen')).toHaveAttribute('data-display', 'normal');
  server.voice('다음');
  await expect(page.getByTestId('run-step-text')).toHaveText(second.korean ?? second.source);
  server.voice('실험 일시정지');
  await expect(page.getByTestId('run-paused')).toBeVisible();
  server.voice('다시 시작');
  await expect(page.getByTestId('run-paused')).toHaveCount(0);
  server.voice('종료');
  expect(server.ended).toBe(false);
  await expect(page.getByTestId('run-screen')).toBeVisible();
  server.voice('네');
  await expect(page.getByTestId('run-ended')).toContainText('실험을 멈췄어요');

  expect(await page.evaluate(() => (window as any).__hood1Touches)).toBe(0);
  expect(server.wrongVoiceMutations).toBe(0);
}
