import { test, expect } from './routes';
import { APP, ENTRY_ANALYSING, ENTRY_FAILED, ENTRY_READY, ENTRY_REVIEW, mockAccounts, mockCatalog } from './app-rx0';

// Lane RX0 (2026-10-24): walks of the new screen at /app/ on the desktop and
// the phone (the tablet walk is tablet-app-rx0.spec.ts). The CI server runs
// in dev mode with an empty catalog; what a walk needs beyond that is mocked.

test.describe('The new screen (lane RX0)', () => {
  test('/app/ serves the built page with the name, the menu and no experiment bar', async ({ page }) => {
    await page.goto(APP);
    await expect(page).toHaveTitle('Voiney Lab');
    await expect(page.getByRole('navigation', { name: '화면 고르기' })).toBeVisible();
    for (const label of ['홈', '프로토콜', '실험', '기록', '설정']) {
      await expect(page.getByRole('navigation').getByText(label, { exact: true })).toBeVisible();
    }
    // The old screen's experiment bar is not here while no experiment runs.
    for (const word of ['일시정지', '종료', '안전 자료 확인 중', '프로토콜을 고르세요', '지금 기준']) {
      await expect(page.getByText(word, { exact: true })).toHaveCount(0);
    }
    // The CI server's dev identity: a quiet chip, no logout.
    await expect(page.getByTestId('dev-chip')).toHaveText('개발 모드');
    await expect(page.getByTestId('logout-open')).toHaveCount(0);
    // The final logo (an image from this server's brand folder), the tab icon and the manifest beside it.
    const logo = page.getByRole('img', { name: 'Voiney Lab' }).first();
    await expect(logo).toBeVisible();
    expect(await logo.evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
    expect((await logo.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(16);
    expect(await page.locator('link[rel="icon"]').getAttribute('href')).toBe('/app/brand/voiney-lab_favicon_32.png');
    expect(await page.locator('link[rel="manifest"]').getAttribute('href')).toBe('/app/manifest.webmanifest');
    const manifest = await page.request.get('/app/manifest.webmanifest');
    expect(manifest.status()).toBe(200);
    expect((await manifest.json()).name).toBe('Voiney Lab');
    expect((await page.request.get('/app/brand/voiney-lab_app-icon_180.png')).status()).toBe(200);
    // Fonts come from this server, never a CDN.
    const fontRequests: string[] = [];
    page.on('request', (request) => { if (request.resourceType() === 'font') fontRequests.push(request.url()); });
    await page.reload();
    await expect.poll(() => fontRequests.length).toBeGreaterThan(0);
    for (const url of fontRequests) expect(new URL(url).origin).toBe(new URL(page.url()).origin);
  });

  test('a first user (empty catalog, the CI server itself) sees one card: 첫 프로토콜 올리기', async ({ page }) => {
    await page.goto(APP);
    const card = page.locator('.big-card');
    await expect(card).toHaveCount(1);
    await expect(card).toContainText('첫 프로토콜 올리기');
    // Lane RX1: the upload is the new screen's own.
    await expect(card).toHaveAttribute('href', '#/upload');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test('with runnable protocols and nothing to review the home offers 실험 시작 with three small actions', async ({ page }) => {
    await mockCatalog(page, [ENTRY_FAILED, ENTRY_ANALYSING, ENTRY_READY]);
    await page.goto(APP);
    await expect(page.locator('.big-card')).toContainText('실험 시작');
    await expect(page.locator('.big-card')).toContainText('바로 쓸 수 있는 프로토콜 1개');
    // Lane RX2: the start card opens the new screen's start flow.
    await expect(page.locator('.big-card')).toHaveAttribute('href', '#/start');
    await expect(page.locator('.secondary-actions a')).toHaveText(['프로토콜 보기', '올리기', '지난 기록']);
    await page.getByRole('navigation').getByText('프로토콜', { exact: true }).click();
    await expect(page).toHaveURL(/#\/protocols$/);
    await expect(page.locator('.row')).toHaveCount(3);
  });

  test('the list shows a chip and the next action per row, runnable first', async ({ page }) => {
    // A review-pending protocol is in this catalog too, so the home would lead with 확인하기; the list is opened directly.
    await mockCatalog(page, [ENTRY_FAILED, ENTRY_REVIEW, ENTRY_ANALYSING, ENTRY_READY]);
    await page.goto(`${APP}#/protocols`);
    const rows = page.locator('.row');
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(0)).toContainText('바로 쓸 수 있어요');
    // Lane RX2: the start goes to the new screen's start flow (startHref).
    await expect(rows.nth(0).getByRole('link', { name: '시작하기' })).toHaveAttribute('href', '#/start/p-rx0-ready');
    await expect(rows.nth(1)).toContainText('분석 중 · 3분 지남');
    // Lane RX1: a row being analysed opens its progress screen; it has no start or review button.
    await expect(rows.nth(1).getByRole('link', { name: '진행 보기' })).toHaveAttribute('href', '#/protocols/p-rx0-analysing/analysis');
    await expect(rows.nth(2)).toContainText('분석 실패 — 다시 시도');
    await expect(rows.nth(3)).toContainText('확인 8개 남음');
    await expect(rows.nth(3).getByRole('link', { name: '확인하기' })).toHaveAttribute('href', '#/protocols/p-rx0-review/review');
    for (const word of ['검토 대기', '실행 가능', '실행 불가', '카탈로그']) await expect(page.getByText(word)).toHaveCount(0);
    // Every target is at least 44 px tall.
    for (const link of await page.locator('.row .btn, .nav a, .screen-head .btn').all()) {
      expect((await link.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
  });

  test('a review-pending protocol becomes the home card 확인하기 with the count', async ({ page }) => {
    await mockCatalog(page, [ENTRY_READY, ENTRY_REVIEW]);
    await page.goto(APP);
    const card = page.locator('.big-card');
    await expect(card).toContainText('확인하기');
    await expect(card).toContainText('Western blot · 확인할 것 8개');
    await expect(card).toContainText('8가지만 확인하면 실험에 쓸 수 있어요.');
    await expect(card).toHaveAttribute('href', '#/protocols/p-rx0-review/review');
  });

  test('my saved experiment becomes 이어서 하기 · N단계부터', async ({ page }) => {
    await mockCatalog(page, [ENTRY_READY], [{ session_id: 'exp-rx0', protocol_id: 'p-rx0-ready', protocol_title: 'In-gel digestion', status: 'paused', current_step_label: '4', owner_principal_id: 'acc-rx0', started_at: '2026-10-24T01:00:00+00:00' }]);
    await page.goto(APP);
    await expect(page.locator('.big-card')).toContainText('이어서 하기');
    await expect(page.locator('.big-card')).toContainText('In-gel digestion · 4단계부터');
    // Lane RX2: 이어서 하기 opens the new start flow's continuing start for that experiment.
    await expect(page.locator('.big-card')).toHaveAttribute('href', '#/start/p-rx0-ready?resume=exp-rx0');
  });

  test('accounts mode: the login with the name, one button and the server words for a wrong password and a lock', async ({ page }) => {
    await mockAccounts(page, false);
    let attempts = 0;
    await page.route('**/api/auth/login', (route) => {
      attempts += 1;
      if (attempts === 1) return route.fulfill({ status: 401, json: { detail: 'login_refused' } });
      return route.fulfill({ status: 429, headers: { 'Retry-After': '600' }, json: { detail: 'login_locked' } });
    });
    await page.goto(APP);
    await expect(page.getByText('장갑을 낀 채 말로 진행하는 실험 안내')).toBeVisible();
    await expect(page.getByText(/1 \/ 6/)).toHaveCount(0);
    await expect(page.getByRole('button', { name: '로그인하기' })).toBeVisible();
    await page.getByRole('button', { name: '로그인하기' }).click();
    await expect(page.getByTestId('auth-error')).toHaveText('이름과 비밀번호를 적어 주세요.');
    await page.getByLabel('이름').fill('재준');
    await page.getByLabel('비밀번호').fill('wrong-pass');
    await page.getByRole('button', { name: '로그인하기' }).click();
    await expect(page.getByTestId('auth-error')).toHaveText('이름이나 비밀번호가 맞지 않아요.');
    await page.getByRole('button', { name: '로그인하기' }).click();
    await expect(page.getByTestId('auth-error')).toHaveText('여러 번 틀려 잠시 막혔어요. 10분 뒤에 다시 해 주세요.');
  });

  test('accounts mode: the frame names the account and logging out asks first', async ({ page }) => {
    let signedIn = true;
    await page.route('**/api/auth/session', (route) =>
      route.fulfill({ json: signedIn ? { mode: 'accounts', signed_in: true, account: { account_id: 'acc-rx0', name: '재준', organization_id: 'lab-a', admin: true } } : { mode: 'accounts', signed_in: false, account: null } }),
    );
    await page.route('**/api/auth/logout', (route) => { signedIn = false; return route.fulfill({ json: { signed_out: true } }); });
    await mockCatalog(page, [ENTRY_READY]);
    // Lane RX2 (decision 6): the first guide comes once after a login; this account has seen it.
    await page.addInitScript(() => localStorage.setItem('voiney-lab.app-welcome:acc-rx0', 'done'));
    await page.goto(APP);
    await expect(page.getByTestId('account-line')).toHaveText('재준 · 연구실 lab-a');
    await expect(page.getByText('관리자', { exact: true })).toBeVisible();
    await page.getByTestId('logout-open').click();
    await expect(page.getByRole('dialog')).toContainText('로그아웃할까요?');
    await page.getByTestId('logout-confirm').click();
    await expect(page.getByRole('button', { name: '로그인하기' })).toBeVisible();
  });

  test('a route the new screen has not taken over opens on the old screen unchanged', async ({ page }) => {
    // Known protocol details belong to the new screen; an unknown route retains its escape.
    // The old router preserves the URL and shows home for an unknown nested path.
    await page.goto(`${APP}#/protocols/source-legacy/unowned`);
    await page.waitForURL(/\/index\.html\?legacy=1#\/protocols\/source-legacy\/unowned$/);
    await expect(page.locator('#tab-home')).toBeVisible();
  });

  test('the default root is the current screen; /app opens the new one', async ({ page }) => {
    const root = await page.request.get('/', { maxRedirects: 0 });
    expect(root.status()).toBe(200);
    expect(await root.text()).toContain('<link rel="manifest" href="/manifest.webmanifest">');
    const app = await page.request.get('/app', { maxRedirects: 0 });
    expect([301, 302, 307, 308]).toContain(app.status());
    expect(app.headers()['location']).toBe('/app/');
  });
});
