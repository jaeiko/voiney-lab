import { expect, type Page, type TestInfo } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { PARITY_DOCUMENTS, parityReview, PARITY_TABLES } from '../../web/src/test/parity';
import { NOTEBOOK } from '../../web/src/test/records';

export async function mockParity(page: Page, owner = true) {
 const writes: Array<{ path: string; body: string | null }> = [];
 const prepared = new Set<string>(); let settings = { question_timing: 'before_start', proactive_mode: 'all', web_lookup: 'on', speaker_sensitivity: 'normal' };
 await page.route('**/api/**', async route => {
  const req = route.request(), url = new URL(req.url()), path = url.pathname;
  if (req.method() !== 'GET') writes.push({ path: path + url.search, body: req.postData() });
  if (path === '/api/auth/session') return route.fulfill({ json: { mode: 'dev', signed_in: true, account: { account_id: 'me', name: '가짜 사용자', organization_id: 'synthetic', admin: true } } });
  if (path === '/api/workspace/session') return route.fulfill({ json: { principal_id: 'me' } });
  if (path === '/api/protocols') return route.fulfill({ json: { protocols: PARITY_DOCUMENTS.map((_, i) => parityReview(i)) } });
  if (path === '/api/voice/voices') return route.fulfill({ json: { voices: [] } });
  if (path === '/api/experimenter/settings') { if (req.method() === 'PUT') settings = { ...settings, ...req.postDataJSON() }; return route.fulfill({ json: { settings } }); }
  const match = /^\/api\/protocols\/([^/]+)(.*)$/.exec(path);
  if (match) {
   const i = PARITY_DOCUMENTS.findIndex(p => p.id === match[1]);
   if (i < 0) return route.fulfill({ status: 404, json: { detail: 'not_found' } });
   const tail = match[2];
   if (tail === '/review' || tail === '') return route.fulfill({ json: parityReview(i) });
   if (tail === '/reagent-tables') return route.fulfill({ json: req.method() === 'POST' ? { reagent_table: PARITY_TABLES.reagent_tables[0], deduplicated: true } : PARITY_TABLES });
   if (tail === '/review-versions/1') return route.fulfill({ json: { version: 1, reviewer_name: '견본 확인자', changes: [{ step_label: '1', kind_ko: '값', original: PARITY_DOCUMENTS[i].previousValue, reviewed: PARITY_DOCUMENTS[i].value, origin: 'reviewer_input', decided_by: '견본 확인자' }] } });
   if (tail === '/reanalysis') return route.fulfill({ status: 202, json: { state: 'queued' } });
   if (tail.startsWith('/review-draft/source-pages/')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="306" height="396"><rect width="306" height="396" fill="white"/><text x="20" y="40">Synthetic source page</text></svg>' });
  }
  const record = /^\/api\/workspace\/experiments\/run-(cells|solution|battery)(.*)$/.exec(path);
  if (record) {
   const id = record[1], session = { session_id: `run-${id}`, protocol_id: id, protocol_title: PARITY_DOCUMENTS.find(p => p.id === id)!.title, owner_principal_id: owner ? 'me' : 'other', version: 7, status: 'stopped' };
   if (!record[2]) return route.fulfill({ json: session });
   if (record[2] === '/timeline') return route.fulfill({ json: { session, timeline: [], recovery: { checkpoints: [{ step_id: 's2', step_label: '2', carried_step_count: 1, stopped_here: true }], not_restored: ['pending_confirmations', 'conversation_history', 'active_timers'] } } });
   if (record[2] === '/reports') return route.fulfill({ json: { reports: [{ report_id: `report-${id}`, ...session }] } });
   if (record[2] === '/checkpoint-restart') return route.fulfill({ status: 201, json: { ...session, session_id: `new-${id}`, status: 'paused' } });
   if (record[2] === '/observations' || record[2] === '/evidence') return route.fulfill({ status: 201, json: {} });
  }
  const report = /^\/api\/experiment-reports\/report-(cells|solution|battery)\/(notebook|prose)$/.exec(path);
  if (report) {
   if (report[2] === 'notebook') return route.fulfill({ json: { ...NOTEBOOK, report: { report_id: `report-${report[1]}`, session_id: `run-${report[1]}`, status: 'stopped', owner_principal_id: owner ? 'me' : 'other' } } });
   if (req.method() === 'POST') prepared.add(report[1]);
   return route.fulfill({ json: { state: 'ready', label: prepared.has(report[1]) ? '문장을 다시 준비했어요.' : '문장이 준비됐어요.', new_records: 0 } });
  }
  return route.fulfill({ status: 404, json: { detail: 'not_found' } });
 });
 return writes;
}
async function shot(page: Page, info: TestInfo, name: string) {
 if (!process.env.RX5_SHOTS) return;
 await mkdir(process.env.RX5_SHOTS, { recursive: true });
 await page.screenshot({ path: join(process.env.RX5_SHOTS, `${info.project.name.replace(/\s+/g, '-').toLowerCase()}-${name}.png`), fullPage: true, animations: 'disabled' });
}
async function checkLayout(page: Page) {
 expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
 const sizes = await page.locator('.protocol-detail p, .protocol-detail td, .settings select, .record-actions textarea').evaluateAll(els => els.map(e => parseFloat(getComputedStyle(e).fontSize)));
 expect(sizes.every(n => n >= 16)).toBe(true);
}
export async function parityWalk(page: Page, info: TestInfo) {
 const writes = await mockParity(page);
 // The unchanged previous screen provides the before photos, on the same synthetic data.
 await page.goto('/index.html?legacy=1#/protocols/solution');
 await expect(page.locator('#protocol-detail-view')).toBeVisible(); await shot(page, info, 'before-protocol');
 await page.goto('/index.html?legacy=1#/records/run-solution');
 await expect(page.locator('#records-detail-view')).toBeVisible(); await shot(page, info, 'before-record');
 await page.goto('/index.html?legacy=1#/settings'); await shot(page, info, 'before-settings');
 writes.length = 0;
 for (const p of PARITY_DOCUMENTS) {
  await page.goto(`/app/#/protocols/${p.id}`);
  await expect(page.getByTestId('protocol-detail')).toContainText(p.instruction);
  await expect(page.getByText(p.condition, { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '원문 2쪽 열기' })).toHaveAttribute('target', '_blank');
  await page.getByRole('button', { name: /검토본 v1/ }).click();
  await expect(page.getByTestId('version-changes')).toContainText(`${p.previousValue} → ${p.value}`);
  await page.getByText('synthetic.xlsx', { exact: true }).click();
  await expect(page.locator('td').getByText('0.15 M', { exact: true })).toBeVisible();
  await checkLayout(page); await shot(page, info, `after-${p.id}-protocol`);
  expect(writes).toEqual([]);
  await page.goto(`/app/#/records/run-${p.id}`);
  await page.getByLabel('메모 내용').fill('가짜 자료 메모');
  await page.getByLabel('메모 종류').selectOption('measurement');
  await page.getByRole('button', { name: '메모 남기기' }).click();
  await expect(page.getByText('기록했어요.', { exact: true })).toBeVisible();
  await page.getByLabel('사진·문서 붙이기').setInputFiles({ name: 'synthetic.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-synthetic') });
  await expect(page.getByText('파일을 붙였어요.', { exact: true })).toBeVisible();
  await page.getByLabel('저장해 둔 지점').selectOption('s2');
  await page.getByRole('button', { name: '이 지점부터 새로 시작하기' }).click();
  await expect(page.getByRole('dialog')).toContainText('타이머');
  await shot(page, info, `after-${p.id}-restart`);
  await page.getByRole('button', { name: '그만두기', exact: true }).click();
  await checkLayout(page); await shot(page, info, `after-${p.id}-record`);
  await page.getByRole('link', { name: '실험 노트 보기', exact: true }).click();
  await expect(page.getByText('문장이 준비됐어요.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '보고서 문장 다시 만들기' }).click();
  await expect(page.getByText('문장을 다시 준비했어요.', { exact: true })).toBeVisible();
  await checkLayout(page); await shot(page, info, `after-${p.id}-notebook`);
  writes.length = 0;
 }
 await page.route('**/api/protocols/*/review', route => {
  const id = new URL(route.request().url()).pathname.split('/').at(-2);
  const index = PARITY_DOCUMENTS.findIndex(p => p.id === id);
  return route.fulfill({ json: { ...parityReview(index), available_for_execution: false, review_pending: { needs_review_count: 2 } } });
 });
 for (const p of PARITY_DOCUMENTS) {
  await page.goto(`/app/#/protocols/${p.id}`);
  await expect(page.getByText('아직 원문과 맞춰 확인할 분석 내용이 있어요. 확인 화면에서 원문과 함께 살펴봐 주세요.', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '이 프로토콜로 시작', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: '확인하기', exact: true })).toHaveAttribute('href', `#/protocols/${p.id}/review`);
  expect(writes).toEqual([]);
  await checkLayout(page); await shot(page, info, `after-${p.id}-protocol-pending`);
 }
 await page.goto('/app/#/settings');
 await page.getByLabel('원문이 "경우에 따라" 할 때').selectOption('during');
 await page.getByLabel('먼저 말해 주기').selectOption('needed');
 await page.getByLabel('웹에서 찾아보기').selectOption('off');
 await page.getByLabel('옆 사람 말 거르는 정도').selectOption('low');
 await page.getByLabel('내가 말하는 언어').selectOption('auto');
 await page.getByLabel('작은 소리 키우기').selectOption('off');
 await checkLayout(page); await shot(page, info, 'after-settings');
 expect(writes.filter(w => w.path === '/api/experimenter/settings')).toHaveLength(4);
}

export async function runToolsWalk(page: Page, info: TestInfo) {
 const { StandIn } = await import('./app-rx2');
 const server = new StandIn({ signedIn: true, protocol: 'eam23' });
 await server.attach(page);
 await page.addInitScript(() => localStorage.setItem('voiney-lab.input-language', 'en'));
 await page.goto(`/app/#/start/${server.protocol.id}`);
 await page.getByTestId('start-next').click();
 await page.getByRole('button', { name: '10 min', exact: true }).click();
 await page.getByRole('button', { name: '56 °C', exact: true }).click();
 await page.getByTestId('start-next').click();
 await page.getByTestId('changes-none').click();
 await page.getByTestId('start-go').click();
 await expect(page.getByTestId('run-screen')).toBeVisible();
 expect(server.starts[0].input_language).toBe('en');
 server.misheard();
 await expect(page.getByText('안 들은 말 1개', { exact: true })).toBeVisible();
 const minimumText = info.project.name.startsWith('Tablet') ? 20 : 16;
 const tools = await page.locator('.run-tools > button, .run-tools > details > summary').evaluateAll(els => ({ goal: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--run-target')), fonts: els.map(e => parseFloat(getComputedStyle(e).fontSize)), heights: els.map(e => e.getBoundingClientRect().height) }));
 expect(tools.fonts.every(size => size >= minimumText), JSON.stringify(tools)).toBe(true);
 expect(tools.heights.every(height => height >= tools.goal), JSON.stringify(tools)).toBe(true);
 await page.getByText('안 들은 말 1개', { exact: true }).click();
 await expect(page.getByText('받아 적은 말이 없어요', { exact: false }).last()).toBeVisible();
 await shot(page, info, 'after-run-dropped');
 const writesBefore = server.controls.length;
 await page.getByRole('button', { name: '설정 열기', exact: true }).click();
 await expect(page.getByRole('dialog')).toContainText('다음 실험부터 적용');
 await expect(page.getByRole('dialog').getByText('저장한 설정은 다음 실험부터 적용돼요. 지금 하던 실험은 그대로예요.', { exact: true })).toHaveCount(1);
 await expect(page.getByRole('dialog').getByTestId('voice-check-start')).toHaveCount(0);
 await expect(page.getByLabel('내가 말하는 언어')).toBeDisabled();
 const targets = await page.getByRole('dialog').locator('.btn, select, .segmented-option, .field > span, p').evaluateAll(els => ({ goal: parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--run-target')), fonts: els.map(e => parseFloat(getComputedStyle(e).fontSize)), heights: els.filter(e => e.matches('.btn, select, .segmented-option')).map(e => e.getBoundingClientRect().height) }));
 expect(targets.fonts.every(size => size >= minimumText), JSON.stringify(targets)).toBe(true);
 expect(targets.heights.every(height => height >= targets.goal), JSON.stringify(targets)).toBe(true);
 await page.getByLabel('먼저 말해 주기').selectOption('needed');
 await expect(page.getByRole('dialog').getByText('저장했어요', { exact: true })).toBeVisible();
 await shot(page, info, 'after-run-settings');
 await page.getByRole('button', { name: '실험으로 돌아가기', exact: true }).click();
 expect(server.controls.slice(writesBefore).filter(c => ['workflow.pause', 'workflow.resume', 'session.start', 'session.stop'].includes(String(c.type)))).toEqual([]);
 await expect(page.getByTestId('run-screen')).toBeVisible();
}
