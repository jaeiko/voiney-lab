import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { CARD_EVIDENCE, draftWith, SOURCE_PAGE, TARGET, UPLOADED } from './app-rx1';
import { StandIn, PROTOCOLS, SESSION } from './app-rx2';
import { NOTEBOOK, REPORT } from '../../web/src/test/records';

// Opt-in documentation capture only. The ordinary CI projects skip this walk.
// Real built UI, synthetic source pages and HTTP/WebSocket responses; no providers.
test.skip(process.env.README_CAPTURE !== '1', 'README media is captured explicitly with readme.config.ts');

class CaptureServer extends StandIn {
  connect(ws: WebSocketRoute) {
    const send = ws.send.bind(ws);
    ws.send = raw => {
      if (typeof raw !== 'string') return send(raw);
      const message = JSON.parse(raw);
      if (message.type === 'session.greeting') message.text = '시험용 견본이에요. 지금 단계를 말로 안내하고 기록을 남겨요.';
      send(JSON.stringify(message));
    };
    super.connect(ws);
  }
}

async function capture(page: Page, device: string, scene: string, fullPage = false) {
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  const dir = process.env.README_RAW_DIR || join(homedir(), 'reports', 'readme-capture');
  await mkdir(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${device}-${scene}.png`), fullPage, animations: 'disabled', caret: 'hide', scale: 'css' });
}

test('upload → pages → evidence card → start → Korean step / timer / records → end → notebook', async ({ page, context }, info) => {
  const device = info.project.name;
  const chemistry = device === 'phone';
  const plan = structuredClone(PROTOCOLS[chemistry ? 'soil' : 'ingel']);
  plan.id = UPLOADED.protocol_id;
  plan.title = chemistry ? '토양 성분 분석 · 시험용 견본' : '단백질 처리 · 시험용 견본';
  if (chemistry) {
    plan.steps[0].korean = plan.inPlaceKorean;
    plan.steps[1].korean = '플라스크에 흙 시료와 KCL (1M) 100 mL를 넣습니다.';
  }
  const server = new CaptureServer({ signedIn: true });
  server.protocol = plan;
  const observation = chemistry ? '시료가 맑아 보여요' : '젤 조각이 투명해졌어요';
  const timeline = server.timeline.bind(server);
  server.timeline = () => {
    const value = timeline();
    value.timeline = value.timeline.map(event => {
      const row = event as { observation?: { content: string } };
      return row.observation ? { ...row, step_label: chemistry ? '1' : '3', observation: { ...row.observation, content: observation } } : row;
    });
    return value;
  };
  const experiment = server.experiment.bind(server);
  server.experiment = () => ({ ...experiment(), owner_principal_id: 'synthetic' });
  const outside: string[] = [];
  const unexpectedApi: string[] = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://127.0.0.1:8046') {
      outside.push(url.origin); return route.abort('blockedbyclient');
    }
    if (url.pathname.startsWith('/api/')) {
      unexpectedApi.push(url.pathname); return route.fulfill({ status: 404, json: { detail: 'capture_unhandled_api' } });
    }
    return route.continue();
  });
  page.on('response', response => {
    if (response.status() === 404 && new URL(response.url()).pathname.startsWith('/api/')) unexpectedApi.push(new URL(response.url()).pathname);
  });
  await server.attach(page);
  // All identity in these images is fictional. No user identifiers or real documents.
  await page.route('**/api/auth/session', route => route.fulfill({ json: { mode: 'accounts', signed_in: true, account: { account_id: 'synthetic', name: '시험용 연구자', admin: false } } }));
  await page.route('**/api/workspace/session', route => route.fulfill({ json: { principal_id: 'synthetic', display_name: '시험용 연구자' } }));

  let analysed = false;
  let decided = false;
  let registered = false;
  const source = { ...SOURCE_PAGE(1), image_dpi: 72 };
  // Source-page pixels and line geometry come from the same synthetic lines.
  // The chemistry lines are exact existing walk strings; these are not a runnable method.
  if (chemistry) {
    source.lines = source.lines.slice(0, 4).map((line, i) => ({ ...line, text: [
      'Synthetic soil analysis example', 'NON-OPERATIONAL TEST DATA', plan.steps[0].source, plan.steps[1].source,
    ][i] }));
  }
  const card = { ...CARD_EVIDENCE, source: { ...CARD_EVIDENCE.source, excerpt: source.lines[3].text }, shown: source.lines[3].text, current: source.lines[3].text };
  const entry = () => ({ ...UPLOADED, title: plan.title, source_filename: chemistry ? 'soil-example.pdf' : 'protein-example.pdf',
    available_for_execution: registered, lifecycle_state: registered ? 'ready' : analysed ? 'review_pending' : 'uploaded',
    review_pending: analysed && !registered ? { needs_review_count: 1 } : null,
    analysis_status: analysed && !registered ? 'review_pending' : 'structured_analysis_ready',
    analysis_run: { state: analysed ? 'review_pending' : 'uploaded', reanalysis: false, failure_code: null },
  });
  await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [entry()] } }));
  await page.route(/\/api\/protocols\?filename=/, route => route.fulfill({ status: 201, json: { protocol: entry(), deduplicated: false, ocr: { state: 'not_required', automatic: false } } }));
  await page.route(`**/api/protocols/${plan.id}`, route => route.fulfill({ json: entry() }));
  await page.route(`**/api/protocols/${plan.id}/review-draft/source-pages`, route => route.fulfill({ json: { protocol_id: plan.id, page_count: 3, thumbnail_dpi: 36, image_dpi: 72 } }));
  await page.route(/\/review-draft\/source-pages\/\d+\/image/, route => {
    const escape = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="595" height="842" viewBox="0 0 595 842"><rect width="595" height="842" fill="white"/><text x="72" y="35" font-size="10" fill="#666">SYNTHETIC EXAMPLE / NOT FOR LAB USE</text>${source.lines.map(line => `<text x="${line.box[0]}" y="${line.box[3]}" font-family="sans-serif" font-size="10">${escape(line.text)}</text>`).join('')}</svg>`;
    return route.fulfill({ contentType: 'image/svg+xml', body: svg });
  });
  await page.route(/\/review-draft\/source-pages\/\d+$/, route => route.fulfill({ json: { ...source, page: Number(new URL(route.request().url()).pathname.split('/').pop()) } }));
  await page.route(/\/api\/protocols\/p-rx1-new\/analysis(\?.*)?$/, route => {
    expect(route.request().url()).toContain('first_page=1&last_page=2');
    analysed = true; return route.fulfill({ status: 202, json: { ...entry(), analysis_request_accepted: true } });
  });
  const draft = () => ({ ...draftWith([{ ...card, decided }], decided ? null : 0), title: plan.title,
    counts: { items: 1, needs_review: 1, decided: decided ? 1 : 0, undecided: decided ? 0 : 1, changed: 0 }, registrable: decided,
  });
  await page.route(`**/api/protocols/${plan.id}/review-draft`, route => route.fulfill({ json: draft() }));
  await page.route(`**/api/protocols/${plan.id}/review-decisions`, route => {
    expect(route.request().postDataJSON()).toMatchObject({ item_id: card.item_id, action: 'confirm', source_lines: { page: 1, first_line: 4, last_line: 4 } });
    decided = true; return route.fulfill({ json: draft() });
  });
  await page.route(`**/api/protocols/${plan.id}/review-versions`, route => { registered = true; return route.fulfill({ status: 201, json: { version: 1 } }); });
  const options = chemistry ? ['10 g', '100 mL'] : ['10 min', '37 °C'];
  const questions = options.map((value, i) => ({ question_id: `q${i}`, progress_ko: `${i + 1} / 2`, question_ko: `${i + 1}번째 원문 값은 무엇인가요?`,
    source: { page: 1, excerpt: chemistry ? plan.steps[i].source : source.lines[3].text, lines: { first_line: i + 3, last_line: i + 3 } },
    choices: [{ choice_id: `c${i}`, label_ko: value }],
  }));
  await page.route(`**/api/protocols/${plan.id}/start-check`, route => route.fulfill({ json: { enabled: true, protocol_id: plan.id, target: TARGET, questions } }));
  await page.route(`**/api/protocols/${plan.id}/review`, route => route.fulfill({ json: { protocol_id: plan.id, available_for_execution: true, safety_notices: [], execution_notices: [], safety_scope: { usage_scope: 'source_protocol_only', source_protocol_only: true } } }));

  await page.goto('/app/#/upload');
  await expect(page.getByRole('heading', { name: '프로토콜 올리기' })).toBeVisible();
  await capture(page, device, '01-upload');
  await page.getByRole('radio', { name: '예' }).check();
  await page.getByLabel('출처 메모').fill('공개된 합성 시험 자료');
  await page.getByTestId('file-input').setInputFiles({ name: chemistry ? 'soil-example.pdf' : 'protein-example.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 synthetic capture; not a source document') });
  await page.getByTestId('pages-pick').click();
  await page.locator('.page-thumb').nth(0).click();
  await page.locator('.page-thumb').nth(1).click();
  await expect(page.getByTestId('page-picker-hint')).toContainText('1–2쪽');
  await capture(page, device, '02-pages');
  await page.getByTestId('pages-start').click();
  await page.getByTestId('analysis-review-link').click();
  await expect(page.getByTestId('review-progress')).toContainText('0 / 1');
  await capture(page, device, '03-review', chemistry);
  await page.locator('.review-line[data-line="4"]').click();
  await page.getByRole('button', { name: '고른 줄이 맞아요' }).click();
  await expect(page.getByTestId('review-done')).toBeVisible();
  await page.getByTestId('review-start').click();
  await expect(page.getByTestId('start-screen')).toBeVisible();
  await page.getByTestId('start-next').click();
  await expect(page.getByTestId('check-progress')).toContainText('1번째');
  await capture(page, device, '04-start');
  for (const value of options) await page.getByRole('button', { name: value, exact: true }).click();
  await page.getByTestId('start-next').click();
  await page.getByTestId('changes-none').click();
  await page.getByTestId('start-go').click();
  await expect(page.getByTestId('run-step-text')).toHaveAttribute('data-lang', 'ko');
  if (chemistry) server.nextStep();
  await expect(page.getByTestId('run-step-text')).toHaveText(plan.steps[chemistry ? 1 : 0].korean!);
  await expect(page.getByTestId('run-timer')).toBeVisible();
  await expect(page.getByTestId('record-memo')).toBeInViewport();
  await expect(page.getByTestId('record-photo')).toBeInViewport();
  await capture(page, device, '05-run');
  await page.getByTestId('run-stop').click();
  await page.getByTestId('run-stop-confirm').click();
  await expect(page.getByTestId('run-ended')).toContainText('실험을 멈췄어요');
  await capture(page, device, '06-end');

  // Open this run's actual note link; keep the synthetic report consistent with a stopped run.
  const report = { ...REPORT, report_id: server.report, protocol_title: plan.title, status: 'stopped', owner_principal_id: 'synthetic' };
  await page.route(`**/api/experiment-reports/${server.report}/notebook`, route => route.fulfill({ json: {
    ...NOTEBOOK, report, events: [], review: [], notebook: { ...NOTEBOOK.notebook,
      title: plan.title, header_rows: [['프로토콜', plan.title], ['상태', '중간에 멈춘 시험용 실험']],
      purpose_words: '화면 흐름을 확인하는 시험용 견본', samples: [], condition_rows: [], differences: [],
      observations: [{ label: chemistry ? '1' : '3', at: '10:01', kind: '관찰', shown: observation, text: observation }],
      result_words: '', result: '', next_change: '', source_words: '시험용 가짜 자료',
    },
  } }));
  await page.route(`**/api/experiment-reports/${server.report}/sharing`, route => route.fulfill({ json: {
    report_id: server.report, shared: false, changed_at: null,
  } }));
  await page.route(`**/api/experiment-reports/${server.report}/comparisons`, route => route.fulfill({ json: {
    anchor_report_id: server.report, records: [], permission: { judgment: 'none' },
  } }));
  // The current end link leads through the record detail before the notebook.
  await page.route(`**/api/workspace/experiments/${SESSION}/reports`, route => route.fulfill({ json: { reports: [report] } }));
  await page.getByRole('link', { name: '실험 노트 보기', exact: true }).click();
  if (!(await page.getByTestId('notebook-screen').count())) await page.getByRole('link', { name: '실험 노트 보기', exact: true }).click();
  await expect(page.getByTestId('notebook-screen')).toBeVisible();
  await capture(page, device, '07-notebook', true);
  expect(outside).toEqual([]);
  expect(unexpectedApi).toEqual([]);
});
