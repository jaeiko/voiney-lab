import { test, expect } from '@playwright/test';

test.describe.configure({ timeout: 45_000 });

async function openAdminWorkspace(page) {
  await page.goto('/');
  await page.locator('#workspace-admin').waitFor({ state: 'visible', timeout: 20_000 });
  await page.locator('#workspace-admin').click();
  await expect(page.locator('#admin-workspace')).toBeVisible();
  await expect(page.locator('#researcher-workspace')).toBeHidden();
  await expect(page.locator('#reviewer-workspace')).toBeHidden();
}

test.describe('Lab Admin workspace', () => {
  test('answers the four admin jobs up front and keeps setup and audit behind disclosure', async ({ page }) => {
    await openAdminWorkspace(page);
    await expect(page.locator('#admin-connectors')).not.toHaveText('', { timeout: 15_000 });
    await expect(page.locator('#admin-memberships')).not.toHaveText('', { timeout: 15_000 });

    // Default view: who, what is connected, security posture, operational state.
    await expect(page.locator('#admin-memberships')).toBeVisible();
    await expect(page.locator('#admin-connectors')).toBeVisible();
    await expect(page.locator('#admin-security-summary')).not.toHaveText('', { timeout: 15_000 });
    await expect(page.locator('#admin-attention')).toBeVisible();
    await expect(page.locator('#admin-attention')).not.toHaveText('', { timeout: 15_000 });
    await expect(page.locator('#admin-retention-days')).toBeVisible();
    await expect(page.locator('#admin-metrics')).toBeVisible();
    expect(await page.locator('#admin-workspace').innerText()).not.toContain('dev-local-admin');

    // Setup forms and audit streams are one click away, not always on screen.
    await expect(page.locator('#admin-member-id')).toBeHidden();
    await expect(page.locator('#admin-connector-create')).toBeHidden();
    await expect(page.locator('#admin-security-activity')).toBeHidden();
  });

  test('every critical admin capability is still reachable after the cleanup', async ({ page }) => {
    await openAdminWorkspace(page);
    await expect(page.locator('#admin-memberships')).not.toHaveText('', { timeout: 15_000 });
    await page.locator('#admin-workspace details.admin-disclosure', { hasText: '구성원 추가' }).click();
    await expect(page.locator('#admin-member-id')).toBeVisible();
    await expect(page.locator('#admin-member-role')).toBeVisible();
    await expect(page.locator('#admin-member-save')).toBeVisible();
    await expect(page.locator('#admin-permission-preview')).toContainText('허용 작업');

    await page.locator('#admin-workspace details.admin-disclosure', { hasText: '새 연결 설정' }).click();
    await expect(page.locator('.admin-setup-flow li')).toHaveCount(5);
    await expect(page.locator('#admin-connector-kind')).toBeVisible();
    await expect(page.locator('#admin-connector-secret')).toBeVisible();
    await expect(page.locator('#admin-connector-create')).toBeVisible();

    await page.locator('#admin-workspace details.admin-disclosure', { hasText: '로그인·권한 변경 기록' }).click();
    await expect(page.locator('#admin-security-activity')).toBeVisible();

    await expect(page.locator('#admin-retention-save')).toBeVisible();
    await expect(page.locator('#admin-metrics-load')).toBeVisible();
  });

  test('uses product language and does not expose references or claim a provider test', async ({ page }) => {
    await openAdminWorkspace(page);
    for (const section of await page.locator('#admin-workspace details').all()) {
      await section.evaluate((node) => { node.open = true; });
    }
    const body = await page.locator('#admin-workspace').innerText();
    expect(body).not.toContain('실시간 테스트 완료');
    expect(body).not.toContain('secret://');
    expect(body).not.toContain('Principal ID');
    expect(body).not.toContain('OIDC subject');
    expect(body).toContain('보안 연결 자격 증명');
    expect(body).toContain('외부 제공자와의 실제 통신 성공을 의미하지 않습니다');
  });

  test('dev identity vs OIDC operational auth boundary is stated', async ({ page }) => {
    await openAdminWorkspace(page);
    await page.locator('#admin-workspace details.admin-disclosure', { hasText: '로그인 방식 상세' }).click();
    await expect(page.locator('#admin-workspace')).toContainText('운영 범위는 OIDC가 필수입니다');
  });

  test('requires configuration check before the enable action appears', async ({ page }) => {
    let operationalStatus = 'needs_test';
    await page.route('**/api/workspace/connectors', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ connectors: [{
          connector_id: 'connector-browser-test', connector_kind: 'google_drive',
          display_name: 'Approved source folder', allowed_roots: ['folder:approved'],
          enabled: operationalStatus === 'enabled', credential_configured: true,
          validation_status: operationalStatus === 'needs_test' ? 'untested' : 'configuration_verified',
          operational_status: operationalStatus, last_checked_at: operationalStatus === 'needs_test' ? null : '2026-08-24T12:00:00Z',
          last_failure_code: null,
        }] }),
      });
    });
    await page.route('**/api/workspace/admin/connectors/connector-browser-test/test', async route => {
      operationalStatus = 'ready_to_enable';
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ connector_id: 'connector-browser-test', validation_status: 'configuration_verified', last_checked_at: '2026-08-24T12:00:00Z', last_failure_code: null, test_scope: 'server_configuration', provider_connection_tested: false, next_action: 'enable' }),
      });
    });
    await page.route('**/api/workspace/admin/connectors/connector-browser-test/enabled', async route => {
      operationalStatus = 'enabled';
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ connector_id: 'connector-browser-test', enabled: true, validation_status: 'configuration_verified' }),
      });
    });

    await openAdminWorkspace(page);
    await expect(page.locator('#admin-connectors')).toContainText('구성 검사 필요', { timeout: 15_000 });
    await expect(page.getByRole('button', { name: '연결 활성화' })).toHaveCount(0);
    await page.getByRole('button', { name: '구성 검사' }).click();
    await expect(page.locator('#admin-connector-status')).toContainText('외부 제공자 통신은 아직 검증하지 않았습니다');
    await expect(page.getByRole('button', { name: '연결 활성화' })).toBeVisible();
    await page.getByRole('button', { name: '연결 활성화' }).click();
    await expect(page.locator('#admin-connectors')).toContainText('활성');
  });

  test('short user cards remain independent while long audit activity is bounded', async ({ page }) => {
    await openAdminWorkspace(page);
    await page.evaluate(() => {
      const memberships = document.querySelector('#admin-memberships');
      memberships.replaceChildren(workspaceRow(
        'Local Lab Admin · 랩 관리자',
        '활성 · 계정 식별자 dev-local-admin · 운영 지표 보기 · 사용자 권한 관리',
      ));
      renderAdminSecurity({
        authentication: { production_requirement: 'oidc', current_method: 'development' },
        connections: { enabled: 0, total: 0, needs_test: 0, failed: 0 },
        retention: { analytics_retention_days: 90 },
        activity: Array.from({ length: 24 }, (_, index) => ({
          action: index % 2 ? 'workspace_accessed' : 'retention_updated',
          outcome: 'success', actor_display_name: 'Local Lab Admin',
          created_at: `2026-08-24T18:${String(index).padStart(2, '0')}:00Z`,
          target_kind: index % 2 ? 'workspace' : 'analytics_retention',
        })),
      });
    });

    await page.locator('#admin-workspace details.admin-disclosure', { hasText: '로그인·권한 변경 기록' }).click();
    const layout = await page.evaluate(() => {
      const activity = document.querySelector('#admin-security-activity');
      const hint = document.querySelector('#admin-security-activity + .bounded-list-hint');
      return {
        viewportWidth: window.innerWidth,
        activityClientHeight: activity.clientHeight,
        activityScrollHeight: activity.scrollHeight,
        hintDisplay: getComputedStyle(hint).display,
        horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    });
    if (layout.viewportWidth > 980) {
      expect(layout.activityScrollHeight).toBeGreaterThan(layout.activityClientHeight);
      expect(layout.hintDisplay).not.toBe('none');
    } else {
      expect(layout.activityScrollHeight).toBeLessThanOrEqual(layout.activityClientHeight + 1);
      expect(layout.hintDisplay).toBe('none');
    }
    expect(layout.horizontalOverflow).toBeLessThanOrEqual(1);
    await expect(page.locator('#admin-connectors')).not.toHaveText('');
    await expect(page.locator('#admin-security-activity')).toContainText('Local Lab Admin');
  });

  test('a first-time lab admin can say what the page is for from the header', async ({ page }) => {
    await openAdminWorkspace(page);
    await expect(page.locator('#admin-workspace .workspace-purpose')).toContainText(
      '연구실 구성원, 로그인, 연결 서비스, 데이터 보관과 서비스 상태를 관리하는 곳입니다.');

    // Sections are named after the job they do, not after the storage model.
    for (const heading of ['구성원과 역할', '연결 서비스', '로그인과 데이터 보관', '서비스 상태']) {
      await expect(page.locator('#admin-workspace h2', { hasText: heading })).toBeVisible();
    }

    // Internal identity and storage vocabulary never appears in primary copy.
    for (const section of await page.locator('#admin-workspace details').all()) {
      await section.evaluate((node) => { node.open = true; });
    }
    const body = await page.locator('#admin-workspace').innerText();
    for (const internal of ['테넌트', '비식별 운영 지표', '접근 제어 활동', '인증 경계']) {
      expect(body).not.toContain(internal);
    }
  });
});
