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
  test('shows the five operational groups with advanced controls collapsed', async ({ page }) => {
    await openAdminWorkspace(page);
    await expect(page.locator('#admin-connectors')).not.toHaveText('', { timeout: 15_000 });
    await expect(page.locator('#admin-memberships')).not.toHaveText('', { timeout: 15_000 });
    await expect(page.locator('#admin-connectors')).toBeVisible();
    await expect(page.locator('#admin-memberships')).toBeVisible();
    await expect(page.locator('#admin-metrics')).toBeVisible();
    await expect(page.locator('#admin-retention-days')).toBeVisible();
    await expect(page.locator('#admin-workspace')).toContainText('구성원과 역할');
    await expect(page.locator('#admin-workspace')).toContainText('연결 서비스');
    await expect(page.locator('#admin-workspace')).toContainText('로그인 및 보안');
    await expect(page.locator('#admin-workspace')).toContainText('데이터 및 보관');
    await expect(page.locator('#admin-workspace')).toContainText('서비스 상태 · 운영');
    await expect(page.locator('#admin-permission-preview')).toContainText('허용 작업');
    await expect(page.locator('.admin-setup-flow li')).toHaveCount(5);
    await expect(page.locator('#admin-security-summary')).not.toHaveText('', { timeout: 15_000 });
    await expect(page.locator('#admin-member-id')).toBeHidden();
    await expect(page.locator('#admin-connector-kind')).toBeHidden();
    await expect(page.locator('#admin-security-activity')).toBeHidden();
  });

  test('uses product language by default and keeps technical details accessible', async ({ page }) => {
    await openAdminWorkspace(page);
    const body = await page.locator('#admin-workspace').innerText();
    expect(body).not.toContain('실시간 테스트 완료');
    expect(body).not.toContain('secret://');
    expect(body).not.toContain('Principal ID');
    expect(body).not.toContain('OIDC subject');
    expect(body).not.toContain('dev-local-admin');
    expect(body).not.toContain('OIDC가 필수');
    expect(body).not.toContain('서버에 준비된 로그인 정보');
    expect(body).not.toContain('외부 서비스와 실제로 통신했다는 뜻은 아닙니다');

    await page.getByText('구성원 추가 · 역할 변경').click();
    await expect(page.locator('#admin-member-id')).toBeVisible();
    await page.getByText('새 연결 설정').click();
    await expect(page.locator('#admin-connector-secret')).toBeVisible();
    await expect(page.locator('.admin-test-boundary')).toContainText('외부 서비스와 실제로 통신했다는 뜻은 아닙니다');
    await page.getByText('로그인 방식 기술 정보').click();
    await expect(page.locator('.admin-technical-boundary')).toContainText('OIDC가 필수');
    const memberDetails = page.locator('#admin-memberships .admin-row-details').first();
    if (await memberDetails.count()) {
      await memberDetails.locator('summary').click();
      await expect(memberDetails).toContainText('계정 ID');
    }
  });

  test('dev identity vs OIDC operational auth boundary is disclosed on demand', async ({ page }) => {
    await openAdminWorkspace(page);
    await expect(page.locator('.admin-technical-boundary p')).toBeHidden();
    await page.locator('.admin-technical-boundary summary').click();
    await expect(page.locator('.admin-technical-boundary p')).toContainText('운영 범위는 OIDC가 필수입니다');
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
    await page.waitForLoadState('networkidle');
    await expect(page.locator('#admin-connectors')).toContainText('구성 검사 필요');
    await expect(page.getByRole('button', { name: '연결 활성화' })).toHaveCount(0);
    await page.getByRole('button', { name: '구성 검사' }).click();
    await expect(page.locator('#admin-connector-status')).toContainText('외부 서비스 통신은 아직 검증하지 않았습니다');
    await expect(page.getByRole('button', { name: '연결 활성화' })).toBeVisible();
    await page.getByRole('button', { name: '연결 활성화' }).click();
    await expect(page.locator('#admin-connectors')).toContainText('활성');
  });

  test('desktop and tablet layouts keep one centered column without overflow', async ({ page }) => {
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
      document.querySelector('#admin-security-activity').closest('details').open = true;
    });

    const layout = await page.evaluate(() => {
      const cards = document.querySelectorAll('#admin-workspace .workspace-columns > .workspace-card');
      const membershipCard = cards[0].getBoundingClientRect();
      const connectionCard = cards[1].getBoundingClientRect();
      const activity = document.querySelector('#admin-security-activity');
      const hint = document.querySelector('#admin-security-activity + .bounded-list-hint');
      return {
        viewportWidth: window.innerWidth,
        membershipTop: membershipCard.top,
        membershipLeft: membershipCard.left,
        membershipWidth: membershipCard.width,
        connectionTop: connectionCard.top,
        connectionLeft: connectionCard.left,
        connectionWidth: connectionCard.width,
        membershipCardHeight: membershipCard.height,
        activityClientHeight: activity.clientHeight,
        activityScrollHeight: activity.scrollHeight,
        hintDisplay: getComputedStyle(hint).display,
        horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    });
    expect(layout.connectionTop).toBeGreaterThan(layout.membershipTop + layout.membershipCardHeight - 1);
    expect(Math.abs(layout.membershipLeft - layout.connectionLeft)).toBeLessThanOrEqual(1);
    expect(Math.abs(layout.membershipWidth - layout.connectionWidth)).toBeLessThanOrEqual(1);
    expect(layout.connectionWidth).toBeLessThanOrEqual(1121);
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

  test('tablet layout keeps every operational group within the viewport', async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 900 });
    await openAdminWorkspace(page);
    const layout = await page.evaluate(() => {
      const workspace = document.querySelector('#admin-workspace');
      const cards = [...document.querySelectorAll('#admin-workspace .admin-operations > .workspace-card')];
      return {
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        workspaceWidth: workspace.getBoundingClientRect().width,
        cardWidths: cards.map(card => card.getBoundingClientRect().width),
      };
    });
    expect(layout.overflow).toBeLessThanOrEqual(1);
    expect(layout.cardWidths).toHaveLength(5);
    expect(new Set(layout.cardWidths.map(value => Math.round(value))).size).toBe(1);
    expect(layout.cardWidths[0]).toBeLessThan(layout.workspaceWidth);
  });
});
