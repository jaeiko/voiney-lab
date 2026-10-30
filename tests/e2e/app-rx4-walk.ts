import type { Page, TestInfo } from '@playwright/test';
import { expect } from '@playwright/test';

import { APP, inView, overflow, PROTOCOLS, StandIn } from './app-rx2';
import { shot } from './app-rx2-walk';

// Lane RX4 (2026-10-26): the experiment screen on a protocol of another field than the in-gel sample (rule 10 of the
// lane rules -- a changed behaviour is checked on three or more fields, chemistry among them). The stand-in runs the
// real step sentences of an analysed chemistry / analysis document that has no stored Korean: the step says 번역 준비 중
// and shows its source at an ordinary size (never the step size); the step's label line follows the document's own
// numbering (none, or its own); a pause after the server's generation moved on still resumes; [종료] from the paused
// band ends the run; the end screen names the protocol and its skip goes to the server. Photographed with RX2_SHOTS.

export async function fieldWalk(page: Page, info: TestInfo, key: 'eam23' | 'soil') {
  const plan = PROTOCOLS[key];
  const server = new StandIn({ signedIn: true, protocol: key });
  await server.attach(page);
  // Straight to the start flow for this protocol (the first guide is asked on the home only).
  await page.goto(`${APP}#/start/${encodeURIComponent(plan.id)}`);
  await expect(page.getByTestId('start-screen')).toBeVisible();
  await expect(page.getByTestId('start-protocol')).toContainText(plan.title);
  await page.getByTestId('start-next').click();
  await page.getByRole('button', { name: '10 min', exact: true }).click();
  await page.getByRole('button', { name: '56 °C', exact: true }).click();
  await page.getByTestId('start-next').click();
  await page.getByTestId('changes-none').click();
  await expect(page.getByTestId('start-go')).toHaveText('이 프로토콜로 시작');
  await page.getByTestId('start-go').click();
  await expect(page).toHaveURL(/#\/experiment$/);

  // The step without a stored Korean (decision 2; the server's translation_missing): 번역 준비 중, the source at the
  // next-step size, no fold; the server reads it in place meanwhile (translation_pending).
  server.startTranslating();
  await expect(page.getByTestId('run-step')).toHaveAttribute('aria-busy', 'true');
  const step = page.getByTestId('run-step-text');
  await expect(step).toHaveText(plan.steps[0].source);
  await expect(step).toHaveAttribute('data-lang', 'source');
  await expect(page.getByTestId('run-step-pending')).toHaveText('번역 준비 중');
  await expect(page.getByTestId('run-source')).toHaveCount(0);
  const sizes = await page.evaluate(() => {
    const style = getComputedStyle(document.documentElement);
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
    const token = (name: string) => parseFloat(style.getPropertyValue(name)) * rem;
    return { shown: parseFloat(getComputedStyle(document.querySelector('[data-testid="run-step-text"]')!).fontSize), step: token('--run-step'), next: token('--run-next') };
  });
  expect(sizes.shown, JSON.stringify(sizes)).toBeCloseTo(sizes.next, 0);
  expect(sizes.shown).toBeLessThan(sizes.step);
  // The label line follows the document's numbering: none, or its own (decision 5: never "N단계: N단계").
  await expect(page.locator('#run-step-label')).toHaveText(plan.steps[0].label ? `${plan.steps[0].label}단계 · 전체 ${plan.stepCount}단계` : `전체 ${plan.stepCount}단계`);
  await expect(page.getByTestId('run-next')).toHaveAttribute('data-lang', 'source');
  await expect(page.getByTestId('run-next')).toContainText(plan.steps[1].source);
  expect(await inView(page, '[data-testid="record-memo"]')).toBe(true);
  expect(await overflow(page)).toBeLessThanOrEqual(0);
  await shot(page, info, `30-${key}-run`);

  // Lane KO2's translation_update: the same state again with a checked reading -- the Korean large, the source folded,
  // 번역 준비 중 gone (the reading is the stand-in's, written for this test).
  server.translated({ [plan.steps[0].id]: plan.inPlaceKorean });
  await expect(step).toHaveText(plan.inPlaceKorean);
  await expect(step).toHaveAttribute('data-lang', 'ko');
  await expect(page.getByTestId('run-step-pending')).toHaveCount(0);
  await expect(page.getByTestId('run-source-text')).toHaveText(plan.steps[0].source, { useInnerText: false });
  await expect(page.getByTestId('run-step')).not.toHaveAttribute('aria-busy', 'true');
  await shot(page, info, `30b-${key}-translated`);

  // The pause after the generation moved on (decision 1): pause -> resume -> pause -> [종료] from the band.
  await page.getByTestId('run-pause').click();
  await expect(page.getByTestId('run-paused')).toBeVisible();
  await page.getByTestId('run-resume').click();
  await expect(page.getByTestId('run-paused')).toHaveCount(0);
  const before = server.generation;
  server.supersede();
  await page.waitForFunction((wanted) => [...(window as any).turnServerGenerations.values()].includes(wanted), before + 1);
  await page.getByTestId('run-pause').click();
  await expect(page.getByTestId('run-paused')).toBeVisible();
  await page.getByTestId('run-resume').click();
  await expect(page.getByTestId('run-paused')).toHaveCount(0);
  expect(server.lastControl('workflow.resume')?.generation).toBe(server.generation);
  await page.getByTestId('run-pause').click();
  await shot(page, info, `31-${key}-paused`);
  await page.getByTestId('run-paused-stop').click();
  await page.getByTestId('run-stop-confirm').click();

  // The end screen for this protocol, skipped.
  await expect(page.getByTestId('run-ended')).toContainText('실험을 멈췄어요');
  await expect(page.getByTestId('end-sub')).toContainText(plan.title);
  await expect(page.getByTestId('end-counts').locator('[data-count="records"] .end-count-number')).toHaveText('1');
  await expect(page.getByTestId('end-counts').locator('[data-count="missed"] .end-count-number')).toHaveText('0');
  await shot(page, info, `32-${key}-ended`);
  await page.getByTestId('end-skip').click();
  await expect(page.getByTestId('end-home')).toBeVisible();
  expect(server.feedback).toEqual([{ report_id: server.report, rating: null, skipped: true, note: null, tags: [] }]);
}
