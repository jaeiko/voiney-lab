import { expect } from '@playwright/test';
import { test } from './routes';
import { mockParity } from './app-rx5-walk';
import { APP, SESSION, StandIn } from './app-rx2';

test.use({ launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] }, permissions: ['microphone'] });
test.describe.configure({ timeout: 600000 });

test('RX5 one ignored-speech server event is counted once without changing the step', async ({ page }) => {
 test.setTimeout(600000);
 const server = new StandIn({ signedIn: true, protocol: 'eam23' });
 await server.attach(page);
 await page.goto(`${APP}#/start/${server.protocol.id}?resume=${SESSION}`);
 await page.getByTestId('start-go').click();
 await expect(page.getByTestId('run-screen')).toBeVisible();
 const text = await page.getByTestId('run-step-text').innerText();
 await expect(page.getByText('안 들은 말 0개', { exact: true })).toBeVisible();
 server.socket!.send(JSON.stringify({ type: 'speech.ignored', configuration_id: server.configurationId, turn_id: 42, generation: server.generation, during_playback: false }));
 await expect(page.getByText('안 들은 말 1개', { exact: true })).toBeVisible();
 await expect(page.getByTestId('run-step-text')).toHaveText(text);
});

test('RX5 reagent cells preserve source coordinates, zero and an uncached formula as text', async ({ page }) => {
 const writes = await mockParity(page);
 await page.route('**/api/protocols/solution/reagent-tables', route => route.fulfill({ json: { reagent_tables: [{ table_id: 'formulas', filename: 'formulas.xlsx', sheets: [{ name: 'Example', cells: [{ ref: 'A1', row: 1, column: 1, display: '', value: null, formula: '=A2*2' }, { ref: 'A2', row: 2, column: 1, display: '0', value: 0 }] }] }] } }));
 await page.goto('/app/#/protocols/solution');
 await page.getByText('formulas.xlsx', { exact: true }).click();
 await expect(page.getByRole('cell', { name: '=A2*2', exact: true })).toBeVisible();
 await expect(page.getByRole('rowheader', { name: 'A1', exact: true })).toBeVisible();
 await expect(page.getByRole('cell', { name: '0', exact: true })).toBeVisible();
 expect(writes).toEqual([]);
});

test('RX5 review history names the selected base, source step and AI suggestion origin', async ({ page }) => {
 const writes = await mockParity(page);
 await page.route('**/api/protocols/solution/review-versions/1', route => route.fulfill({ json: { version: 1, base: { kind: 'analysis' }, reviewer_name: '견본 확인자', registered_on: '2026년 10월 27일', changes: [{ step_label: '1', kind_ko: '농도', original: '0.10 M', reviewed: '0.15 M', origin: 'ai_suggestion', decided_by: '견본 확인자' }] } }));
 await page.goto('/app/#/protocols/solution');
 await page.getByRole('button', { name: /검토본 v1/ }).click();
 const changes = page.getByTestId('version-changes');
 await expect(changes.getByText('AI 제안', { exact: true })).toBeVisible();
 await expect(changes).toContainText('분석한 내용 → 확인한 버전 1');
 await expect(changes).toContainText('1단계');
 await expect(changes).toContainText('0.10 M → 0.15 M');
 expect(writes).toEqual([]);
});

test('RX5 a rejected source value opens the review cards directly', async ({ page }) => {
 const server = new StandIn({ signedIn: true, protocol: 'eam23' });
 await server.attach(page);
 await page.route(`**/api/protocols/${server.protocol.id}/start-check/answers`, route => route.fulfill({ json: { proceed: false, message_ko: '원문 값을 다시 확인해 주세요.', followups: [{ choice_id: 'open_card', label_ko: '확인 화면 열기' }] } }));
 await page.goto(`${APP}#/start/${server.protocol.id}`);
 await page.getByTestId('start-next').click();
 await page.getByRole('button', { name: '10 min', exact: true }).click();
 await expect(page.getByRole('link', { name: '프로토콜 화면에서 확인하기', exact: true })).toHaveAttribute('href', `#/protocols/${server.protocol.id}/review`);
 expect(server.starts).toEqual([]);
});
