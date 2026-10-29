import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

import { test, expect } from './routes';
import { APP, PID, StandIn } from './app-rx2';
import { RUN_OPTIONS } from './app-rx1';

// Lane PAPER1 (2026-10-29): contract-tested on the two touch tablet projects.
// A pasted Methods paragraph takes the existing upload/analysis path, then the
// start flow shows source values, a same-owner last-run value and the explicit
// choice to save that confirmed condition set as a lab revision. No provider
// is called by this walk.

const METHODS = [
  'Add 2 mL of buffer A to the sample.',
  'Incubate at 37 °C for 10 min.',
  'Centrifuge at 500 × g for 5 min.',
].join(' ');

const LAST_CHANGE = {
  item_id: 's2#1', step_id: 's2', step_label: '2', kind: 'time', kind_ko: '시간',
  source_number: '10', source_unit: 'min', source_written: '10 min', source_shown_ko: '10분',
  number: '20', unit: 'min', shown_ko: '20분', words_ko: '20분(오늘 바꾼 값 · 원문 10분)', large: false,
};

test.use({
  launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] },
  permissions: ['microphone'],
});
test.describe.configure({ timeout: 120_000 });

test('pasted Methods to a confirmed last-run condition and start, with counted touches', async ({ page }, info) => {
  const server = new StandIn({ signedIn: true });
  server.protocol = { ...server.protocol, title: '붙여 넣은 Methods 문단', filename: 'pasted-methods.txt' };
  await server.attach(page);

  const uploaded = {
    ...server.entry(), available_for_execution: false, lifecycle_state: 'uploaded',
    analysis_status: 'structured_analysis_ready', readiness_status: 'analysis_required',
    source_filename: 'pasted-methods.txt', title: '붙여 넣은 Methods 문단',
  };
  await page.route(/\/api\/protocols\?filename=pasted-methods\.txt$/, async (route) => {
    expect(route.request().headers()['content-type']).toContain('text/plain');
    expect((await route.request().postDataBuffer())?.toString('utf8')).toBe(METHODS);
    await route.fulfill({ status: 201, json: { protocol: uploaded, deduplicated: false, ocr: { state: 'not_required', automatic: false } } });
  });
  await page.route(`**/api/protocols/${PID}/analysis`, async (route) => {
    await route.fulfill({ status: 202, json: { ...uploaded, analysis_status: 'analyzing', analysis_run: { state: 'analyzing' } } });
  });
  await page.route(`**/api/protocols/${PID}/start-check/changes`, async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fulfill({
        json: {
          ...RUN_OPTIONS,
          protocol_id: PID,
          last_time: { changes: [LAST_CHANGE], report_id: 'ER-PAPER1-LAST', when_ko: '10월 28일' },
          lab_revision: null,
        },
      });
      return;
    }
    const body = route.request().postDataJSON();
    server.changeChoices.push(body);
    await route.fulfill({
      json: {
        change_set_id: 'run-changes-paper1-000001', source: body.source, changes: [LAST_CHANGE],
        lab_revision: { revision_id: 'lab-adapted-paper1-000001', version: 1, label_ko: '연구실판 v1', changes: [LAST_CHANGE] },
      },
    });
  });

  let touches = 0;
  const tap = async (target: ReturnType<typeof page.locator>) => {
    await target.tap();
    touches += 1;
  };
  const type = async (target: ReturnType<typeof page.locator>, text: string) => {
    await target.tap();
    await target.fill(text);
    touches += 1 + [...text].length;
  };

  await page.goto(`${APP}#/upload`);
  await tap(page.getByRole('radio', { name: '예' }));
  await type(page.getByTestId('methods-input'), METHODS);
  await tap(page.getByRole('button', { name: 'Methods 문단 올리기' }));
  await tap(page.getByTestId('pages-all'));
  await expect(page.getByTestId('analysis-ready')).toBeVisible();
  await tap(page.getByTestId('analysis-start'));

  await expect(page.getByTestId('start-screen')).toBeVisible();
  await tap(page.getByTestId('start-next'));
  await tap(page.getByRole('button', { name: '10 min', exact: true }));
  await tap(page.getByRole('button', { name: '56 °C', exact: true }));
  await tap(page.getByTestId('start-next'));
  await tap(page.getByTestId('changes-last-time'));
  await expect(page.getByText('지난 실험(10월 28일)에서 불러왔어요.')).toBeVisible();
  await tap(page.getByRole('checkbox', { name: '이 조건을 연구실판으로 저장' }));
  await tap(page.getByTestId('changes-done'));

  await expect(page.getByText('연구실판 v1로 저장했어요.')).toBeVisible();
  await expect(page.getByTestId('start-go')).toBeVisible();
  const shotDir = process.env.PAPER1_SHOTS;
  if (shotDir) {
    mkdirSync(shotDir, { recursive: true });
    const project = info.project.name.toLowerCase().replace(/\s+/g, '-');
    await page.screenshot({ path: join(shotDir, `paper1-methods-to-start-${project}.png`), fullPage: false });
  }
  await tap(page.getByTestId('start-go'));
  await expect(page).toHaveURL(/#\/experiment$/);
  await expect.poll(() => server.starts.length).toBe(1);

  expect(server.changeChoices).toEqual([expect.objectContaining({
    source: 'last_time', save_as_lab_revision: true,
    changes: [{ item_id: 's2#1', value: '20분' }],
  })]);
  expect(touches).toBe([...METHODS].length + 13);
  info.annotations.push({ type: 'measured-operations', description: `${touches}회 (문자 ${[...METHODS].length} + 화면 터치 13)` });
});
