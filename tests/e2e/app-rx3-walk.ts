import { expect, type Page, type TestInfo } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { RECORDS, REPORT, TIMELINE, NOTEBOOK } from '../../web/src/test/records';

export async function mockRecords(page: Page, admin = false) {
  const writes: string[] = [];
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET') writes.push(path);
    if (path === '/api/auth/session') return route.fulfill({ json: { mode: 'dev', signed_in: true, account: { account_id: 'me', admin, name: '견본 사용자', organization_id: 'lab-example' } } });
    if (path === '/api/workspace/session') return route.fulfill({ json: { principal_id: 'me' } });
    if (path === '/api/protocols') return route.fulfill({ json: { protocols: [] } });
    if (path === '/api/notebook-template') return route.fulfill({ json: { version: 0, sections: [] } });
    if (path === '/api/workspace/experiments') return route.fulfill({ json: { experiments: RECORDS } });
    const record = /^\/api\/workspace\/experiments\/(rx3-[0-2])(?:\/(.*))?$/.exec(path);
    if (record) {
      const row = RECORDS.find(r => r.session_id === record[1])!;
      if (!record[2]) return route.fulfill({ json: row });
      if (record[2] === 'timeline') return route.fulfill({ json: { ...TIMELINE, session: row } });
      if (record[2] === 'reports') return route.fulfill({ json: { reports: [{ ...REPORT, report_id: `report-${row.session_id}`, protocol_title: row.protocol_title, owner_principal_id: row.owner_principal_id }] } });
      if (record[2].startsWith('evidence/')) return route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="96"><rect width="160" height="96" fill="#efefec"/><text x="16" y="52">Synthetic sample</text></svg>' });
    }
    if (/^\/api\/experiment-reports\/report-rx3-[0-2]\/prose$/.test(path)) return route.fulfill({ json: { state: 'ready', label: '보고서 문장이 준비됐어요.' } });
    if (/^\/api\/experiment-reports\/report-rx3-[0-2]\/sharing$/.test(path)) return route.fulfill({ json: { shared: false, changed_at: null } });
    if (/^\/api\/experiment-reports\/report-rx3-[0-2]\/comparisons$/.test(path)) return route.fulfill({ json: { records: [], permission: { judgment: 'none' } } });
    const notebook = /^\/api\/experiment-reports\/report-(rx3-[0-2])\/notebook$/.exec(path);
    if (notebook) {
      const row = RECORDS.find(r => r.session_id === notebook[1])!;
      return route.fulfill({ json: { ...NOTEBOOK, report: { ...REPORT, report_id: `report-${row.session_id}`, protocol_title: row.protocol_title, owner_principal_id: row.owner_principal_id }, notebook: { ...NOTEBOOK.notebook, title: row.protocol_title, header_rows: [['프로토콜', row.protocol_title]] } } });
    }
    if (/\/api\/experiment-reports\/report-rx3-[0-2]\.docx$/.test(path)) return route.fulfill({ contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', headers: { 'Content-Disposition': 'attachment; filename="synthetic-notebook.docx"' }, body: 'synthetic download contract bytes' });
    return route.fallback();
  });
  return writes;
}
async function shot(page: Page, info: TestInfo, name: string) {
  const dir = process.env.RX3_SHOTS;
  if (!dir) return;
  await mkdir(dir, { recursive: true });
  await page.screenshot({ path: join(dir, `${info.project.name.replace(/\s+/g, '-').toLowerCase()}-${name}.png`), fullPage: true, animations: 'disabled', caret: 'hide' });
}
async function noOverflow(page: Page) { expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1); }

export async function recordsWalk(page: Page, info: TestInfo) {
  const writes = await mockRecords(page, true);
  // The retained page is the before picture; its hash route still works through the escape.
  await page.goto('/index.html?legacy=1#/records');
  await expect(page.locator('#records-list .records-row')).toHaveCount(3);
  await shot(page, info, 'before-list');
  await page.locator('#records-list .records-row').first().click();
  await expect(page.locator('#records-detail-view')).toBeVisible();
  await shot(page, info, 'before-detail');

  await page.goto('/app/#/records');
  await expect(page.getByTestId('records-screen')).toBeVisible();
  await expect(page.getByText('평점 4 / 5')).toBeVisible();
  await page.getByLabel('기록 찾기').fill('전극');
  await expect(page.locator('.record-list li')).toHaveCount(1);
  await page.getByLabel('기록 찾기').fill('');
  await page.getByRole('combobox', { name: '상태', exact: true }).selectOption('stopped');
  await expect(page.locator('.record-list li')).toHaveCount(1);
  await page.getByRole('combobox', { name: '상태', exact: true }).selectOption('');
  await noOverflow(page); await shot(page, info, 'after-list');
  for (const [i, row] of RECORDS.entries()) {
    await page.getByRole('link', { name: new RegExp(row.protocol_title) }).click();
    await expect(page.getByTestId('record-detail')).toBeVisible();
    await expect(page.getByText('고친 메모', { exact: true })).toBeVisible();
    await expect(page.getByText('타이머 시작', { exact: true })).toBeVisible();
    await expect(page.getByText('침전이 보여요', { exact: true })).toBeVisible();
    await noOverflow(page); await shot(page, info, `after-${i}-detail`);
    await page.getByRole('link', { name: '실험 노트 보기', exact: true }).click();
    await expect(page.getByTestId('notebook-screen')).toBeVisible();
    await expect(page.getByText('원문 60 s → 실제 90 s [미확인]')).toBeVisible();
    if (i === 2) await expect(page.getByRole('button', { name: '확인하기', exact: true })).toHaveCount(0);
    const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: 'Word로 내려받기', exact: true }).click()]);
    expect(download.suggestedFilename()).toBe(`report-${row.session_id}.docx`);
    expect(await download.failure()).toBeNull();
    await noOverflow(page); await shot(page, info, `after-${i}-notebook`);
    await page.goBack(); await expect(page.getByTestId('record-detail')).toBeVisible();
    await page.getByRole('link', { name: '기록 목록으로' }).click();
  }
  expect(writes).toEqual([]);
}

export async function entryWalk(page: Page) {
  await mockRecords(page);
  for (const path of ['/#/records/rx3-0', '/index.html#/records/rx3-0']) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/app\/#\/records\/rx3-0$/);
    await expect(page.getByTestId('record-detail')).toBeVisible();
  }
  await page.goto('/index.html#/settings/?from=bookmark');
  await expect(page).toHaveURL(/\/app\/#\/settings\/\?from=bookmark$/);
  await expect(page.getByTestId('settings-screen')).toBeVisible();
  // Protocol details now stay on the new screen, including a missing-detail error.
  await page.goto('/app/#/protocols/source-0');
  await expect(page).toHaveURL(/\/app\/#\/protocols\/source-0$/);
  await expect(page.getByTestId('protocol-detail')).toBeVisible();
  await page.goto('/?invite=synthetic-invite#/records');
  await expect(page).toHaveURL(/\/app\/\?invite=synthetic-invite#\/records$/);
  const manifest = await page.request.get('/manifest.webmanifest');
  expect((await manifest.json()).start_url).toBe('/app/');
}

export async function deskControls(page: Page) {
  await mockRecords(page);
  await page.goto('/app/#/records');
  await expect(page.locator('.record-list li')).toHaveCount(3);
  const sizes = await page.locator('.records-filters input, .records-filters select').evaluateAll(elements => elements.map(element => ({
    font: parseFloat(getComputedStyle(element).fontSize), height: element.getBoundingClientRect().height,
  })));
  expect(sizes.length).toBe(6);
  expect(sizes.every(size => size.font >= 16), JSON.stringify(sizes)).toBe(true);
  expect(sizes.every(size => size.height >= 48), JSON.stringify(sizes)).toBe(true);
}
