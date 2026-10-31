import type { Page, TestInfo } from '@playwright/test';
import { expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

import { APP, FIGURE, inView, overflow, overflowing, PROTOCOLS, StandIn } from './app-rx2';

// Lane UI1 (2026-10-31, person's test of the same day): the experiment screen in halves, the end screen's shared
// controls, the records list in the lab's time zone. Walked on protocols of three fields (rule 10 of the lane rules):
// in-gel digestion (biology; the source's figure and a running timer), EAM 2.3 (chemistry / analysis; a web photo with
// its credit and a timer in its last minute), the battery electrode set (materials; a drawn diagram, no timer). The
// stand-in server is app-rx2.ts's: contract-tested, nothing talks to a model, a speech provider or a real server.
//
// UI1_SHOTS=<dir> saves the photos; UI1_BEFORE=1 only walks and photographs (the code before this lane has none of
// the new parts), so the same walk makes the before and after pictures.

const BEFORE = process.env.UI1_BEFORE === '1';
const DRAWN = 'd'.repeat(64);
const WEB = 'e'.repeat(64);
const DRAWN_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="300" viewBox="0 0 480 300"><rect width="480" height="300" fill="#ffffff"/><rect x="60" y="180" width="360" height="40" fill="#efefec" stroke="#1c1c1a" stroke-width="3"/><rect x="60" y="150" width="360" height="30" fill="#76766f"/><text x="240" y="120" font-size="22" text-anchor="middle" fill="#1c1c1a">slurry B · 80 µm</text></svg>`;

export async function ui1Shot(page: Page, info: TestInfo, name: string) {
  const dir = process.env.UI1_SHOTS;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  const project = info.project.name.toLowerCase().replace(/\s+/g, '-');
  await page.screenshot({ path: join(dir, `${project}-${name}.png`), fullPage: false });
}

/** Records of three fields, as the server's list sends them after lane UI1 (newest run first, its zone named). */
export const RECORD_LIST = {
  timezone: 'Asia/Seoul',
  experiments: [
    { session_id: 'ui1-bio', protocol_id: 'p-rx2-1', protocol_title: 'In-gel digestion', status: 'stopped', completed_step_count: 2, started_at: '2026-10-28T07:38:40Z', run_started_at: '2026-10-31T04:40:22Z', unconfirmed_count: 2, report_status: 'stopped', owner_principal_id: 'p-a-rx2', rating: null, rating_state: 'unrated' },
    { session_id: 'ui1-chem', protocol_id: 'p-eam-2-3', protocol_title: 'EAM 2.3 Analytical Solution Preparation', status: 'completed', completed_step_count: 13, started_at: '2026-10-29T13:45:13Z', run_started_at: '2026-10-29T13:45:25Z', unconfirmed_count: 0, report_status: 'completed', owner_principal_id: 'p-a-rx2', rating: 4, rating_state: 'rated' },
    { session_id: 'ui1-battery', protocol_id: 'p-fictional-battery-electrode', protocol_title: '배터리 전극 가짜 자료', status: 'stopped', completed_step_count: 1, started_at: '2026-10-29T07:51:04Z', run_started_at: '2026-10-29T07:51:20Z', unconfirmed_count: 1, report_status: 'stopped', owner_principal_id: 'p-a-rx2', rating: null, rating_state: 'unrated' },
  ],
};

type Box = { x: number; y: number; width: number; height: number };
async function box(page: Page, testId: string): Promise<Box> {
  const found = await page.getByTestId(testId).first().boundingBox();
  expect(found, `${testId} has a box`).not.toBeNull();
  return found!;
}

/** The halves (wide) or the column (narrow), and lane RX4/TAB's sizes kept. */
async function layoutChecks(page: Page, hasTimer: boolean) {
  const width = page.viewportSize()!.width;
  const narrow = width < 900;
  await expect(page.getByTestId('run-screen')).toHaveAttribute('data-layout', narrow ? 'narrow' : 'wide');
  const step = await box(page, 'run-step');
  const visuals = await box(page, (await page.getByTestId('run-figures').count()) ? 'run-figures' : 'run-visuals');
  if (!narrow) {
    const side = await box(page, 'run-side');
    // Two equal halves: the step in the left one, the timer and the pictures in the right one.
    expect(step.x + step.width).toBeLessThanOrEqual(width / 2 + 1);
    expect(Math.abs(side.x - width / 2)).toBeLessThanOrEqual(2);
    expect(Math.abs(side.x + side.width - width)).toBeLessThanOrEqual(2);
    const timers = await box(page, 'run-timers');
    // The timer in the top of the half (a third, never less than the timer itself), the pictures below it filling the rest.
    expect(Math.abs(timers.y - side.y)).toBeLessThanOrEqual(2);
    expect(timers.height).toBeLessThanOrEqual(Math.max(side.height / 3 + 2, hasTimer ? 280 : 0));
    expect(visuals.y).toBeGreaterThanOrEqual(timers.y + timers.height - 1);
    expect(visuals.height).toBeGreaterThanOrEqual(side.height * 0.55);
  } else {
    // One column: the step, the timer right under it, the pictures under the timer.
    if (hasTimer) {
      const timers = await box(page, 'run-timers');
      expect(timers.y).toBeGreaterThanOrEqual(step.y + step.height - 1);
      expect(visuals.y).toBeGreaterThanOrEqual(timers.y + timers.height - 1);
    } else {
      await expect(page.getByTestId('run-timers')).toHaveCount(0);
      expect(visuals.y).toBeGreaterThanOrEqual(step.y + step.height - 1);
    }
  }
  // Lane RX4 / TAB sizes: the step at the Run step size, a single timer's digits at the Run timer size, 64/80 px targets.
  const sizes = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement);
    const rem = parseFloat(root.fontSize);
    const token = (name: string) => {
      const value = root.getPropertyValue(name).trim();
      return parseFloat(value) * (value.endsWith('rem') ? rem : 1);
    };
    const font = (selector: string) => {
      const node = document.querySelector(selector);
      return node ? parseFloat(getComputedStyle(node).fontSize) : null;
    };
    const step = document.querySelector('[data-testid="run-step-text"]')!;
    return {
      step: font('[data-testid="run-step-text"]'), stepToken: token(step.getAttribute('data-length') === 'long' ? '--run-step-min' : step.getAttribute('data-lang') === 'source' ? '--run-next' : '--run-step'),
      digits: font('[data-testid="run-timers"] .run-timer-digits'), timerToken: token('--run-timer'), target: token('--run-target'),
      buttons: [...document.querySelectorAll('.run-bar .run-btn, .run-records-actions .run-btn')].map((node) => node.getBoundingClientRect().height),
    };
  });
  expect(sizes.step).toBeCloseTo(sizes.stepToken, 0);
  if (hasTimer) expect(sizes.digits).toBeCloseTo(sizes.timerToken, 0);
  for (const height of sizes.buttons) expect(height).toBeGreaterThanOrEqual(sizes.target - 1);
  // Every picture stays inside its frame, above its own caption.
  const pictures = await page.evaluate(() => [...document.querySelectorAll('.run-figure')].map((figure) => {
    const image = figure.querySelector('img')!.getBoundingClientRect();
    const frame = figure.querySelector('.run-figure-open')!.getBoundingClientRect();
    const caption = figure.querySelector('figcaption')!.getBoundingClientRect();
    return { inside: image.top >= frame.top - 1 && image.bottom <= frame.bottom + 1 && image.left >= frame.left - 1 && image.right <= frame.right + 1, above: frame.bottom <= caption.top + 1, height: image.height };
  }));
  for (const picture of pictures) {
    expect(picture.inside, JSON.stringify(picture)).toBe(true);
    expect(picture.above, JSON.stringify(picture)).toBe(true);
    expect(picture.height).toBeGreaterThanOrEqual(150);
  }
  expect(await inView(page, '[data-testid="record-memo"]')).toBe(true);
  expect(await inView(page, '[data-testid="record-photo"]')).toBe(true);
  const wide = await overflow(page);
  expect(wide, `horizontal overflow: ${(await overflowing(page)).join('; ')}`).toBeLessThanOrEqual(0);
}

export async function ui1Walk(page: Page, info: TestInfo, key: 'ingel' | 'eam23' | 'battery') {
  const plan = PROTOCOLS[key];
  const server = new StandIn({ signedIn: true, protocol: key });
  await server.attach(page);
  // The pictures a turn can bring (registered after the stand-in's routes: the last route registered answers first).
  await page.route('**/api/drawn-visuals/**', (route) => route.fulfill({ status: 200, contentType: 'image/svg+xml', body: DRAWN_SVG }));
  await page.route('**/api/web-visuals/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: FIGURE }));

  await page.goto(`${APP}#/start/${encodeURIComponent(plan.id)}`);
  await expect(page.getByTestId('start-screen')).toBeVisible();
  await page.getByTestId('start-next').click();
  await page.getByRole('button', { name: '10 min', exact: true }).click();
  await page.getByRole('button', { name: '56 °C', exact: true }).click();
  await page.getByTestId('start-next').click();
  await page.getByTestId('changes-none').click();
  await page.getByTestId('start-go').click();
  await expect(page).toHaveURL(/#\/experiment$/);
  await expect(page.getByTestId('run-step-text')).toBeVisible();

  let hasTimer = Boolean(plan.steps[0].timerSeconds);
  if (key === 'eam23') {
    // The second step, with a timer in its last minute, and a web photo the person asked for.
    server.nextStep();
    hasTimer = true;
    await expect(page.getByTestId('run-step-text')).toHaveText(plan.steps[1].source);
    server.emit({ type: 'protocol.web.result', turn_id: 7, status: 'success', subject: 'digestion vessel liner', spoken: '', screen: '', citations: [], removed_count: 0, spoken_aloud: false,
      photo: { asset_id: WEB, url: `/api/web-visuals/${WEB}`, mime_type: 'image/png', title: 'Microwave digestion vessel', author: 'Lab Photographer', licence: 'CC BY 4.0', page_url: 'https://commons.wikimedia.org/wiki/File:Example.png' } });
  }
  if (key === 'battery') {
    // A diagram drawn from the source's words for this step.
    server.emit({ type: 'protocol.drawing.result', turn_id: 8, status: 'success',
      diagram: { asset_id: DRAWN, url: `/api/drawn-visuals/${DRAWN}`, mime_type: 'image/svg+xml', step_id: server.currentStep().id, label: '슬러리 B 코팅 단면' } });
  }
  if (!BEFORE) {
    if (key === 'eam23') {
      await expect(page.getByTestId('run-figures').locator('[data-origin="web"]')).toBeVisible();
      await expect(page.getByTestId('figure-credit')).toContainText('출처 Wikimedia Commons · 작성자 Lab Photographer · CC BY 4.0');
      await expect(page.getByTestId('figure-credit')).toContainText('실험 PDF 원문 사진이 아니에요');
      await expect(page.getByTestId('figure-credit').getByRole('link')).toHaveAttribute('href', 'https://commons.wikimedia.org/wiki/File:Example.png');
    }
    if (key === 'battery') {
      await expect(page.getByTestId('run-figures').locator('[data-origin="drawn"]')).toBeVisible();
      await expect(page.getByTestId('run-figures')).toContainText('원문을 바탕으로 AI가 그린 그림');
    }
    if (key === 'ingel') await expect(page.getByTestId('run-figures').locator('[data-origin="source"]')).toContainText('원문 그림');
    if (!hasTimer && page.viewportSize()!.width >= 900) await expect(page.getByTestId('run-timers-empty')).toHaveText('이 단계에는 타이머가 없어요.');
    await expect(page.locator('.run-bar [data-testid="run-timer"]')).toHaveCount(0);
    await page.waitForFunction(() => [...document.querySelectorAll('img')].every((img) => img.complete));
    await layoutChecks(page, hasTimer);
  } else {
    await page.waitForTimeout(600);
  }
  await ui1Shot(page, info, `20-${key}-run`);

  // [종료] and the end screen: the shared rating scale and buttons.
  await page.getByTestId('run-stop').click();
  await page.getByTestId('run-stop-confirm').click();
  await expect(page.getByTestId('run-ended')).toBeVisible();
  await expect(page.getByTestId('end-question')).toBeVisible();
  await ui1Shot(page, info, `21-${key}-end`);
  if (!BEFORE) {
    await expect(page.getByTestId('end-when')).toHaveText(/^\d{4}년 \d{1,2}월 \d{1,2}일 \([월화수목금토일]\) \d{2}:\d{2} 시작$/);
    await expect(page.getByRole('radiogroup')).toHaveClass(/rating-scale/);
    await page.getByRole('radio', { name: '편했어요', exact: true }).click();
    await expect(page.getByRole('radio', { name: '편했어요', exact: true })).toHaveAttribute('aria-checked', 'true');
    // Every control on the end screen is 48 px or more, one primary button.
    const heights = await page.evaluate(() => [...document.querySelectorAll('[data-testid="run-ended"] button, [data-testid="run-ended"] a.btn, [data-testid="run-ended"] input')].map((node) => node.getBoundingClientRect().height));
    for (const height of heights) expect(height).toBeGreaterThanOrEqual(47.5);
    await expect(page.getByTestId('run-ended').locator('.btn-primary')).toHaveCount(1);
    await ui1Shot(page, info, `22-${key}-end-rated`);
  }
}

/** The records list after lane UI1: the run's start in Seoul time, newest first, the unconfirmed badge. */
export async function ui1Records(page: Page, info: TestInfo) {
  const server = new StandIn({ signedIn: true, protocol: 'ingel' });
  await server.attach(page);
  await page.route('**/api/workspace/experiments', (route) => route.fulfill({ status: 200, json: RECORD_LIST }));
  await page.route('**/api/notebook-binders/candidates**', (route) => route.fulfill({ status: 200, json: { binders: [], reports: RECORD_LIST.experiments.map((item, at) => ({ report_id: `ER-UI1-${at}`, session_id: item.session_id, protocol_id: item.protocol_id, protocol_title: item.protocol_title, status: item.report_status, started_at: item.run_started_at, ended_at: null })) } }));
  await page.goto(`${APP}#/records`);
  await expect(page.getByTestId('records-screen')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'In-gel digestion' }).first()).toBeVisible();
  if (!BEFORE) {
    const cards = page.getByTestId('record-card');
    await expect(cards).toHaveCount(3);
    await expect(cards.nth(0)).toContainText('2026년 10월 31일 (토) 13:40');
    await expect(cards.nth(0).getByTestId('record-unconfirmed')).toHaveText('확인 안 된 값 2개');
    await expect(cards.nth(1)).toContainText('2026년 10월 29일 (목) 22:45');
    await expect(page.getByText(/UTC/)).toHaveCount(0);
    const heights = await page.evaluate(() => [...document.querySelectorAll('.records-filters .field-control, .binder-candidates .check, .records-screen .btn')].map((node) => node.getBoundingClientRect().height));
    for (const height of heights) expect(height).toBeGreaterThanOrEqual(47.5);
    expect(await overflow(page)).toBeLessThanOrEqual(0);
  }
  await ui1Shot(page, info, '23-records');
  await page.getByTestId('records-screen').locator('.notebook-binder-builder').scrollIntoViewIfNeeded();
  await ui1Shot(page, info, '24-records-binder');
}
