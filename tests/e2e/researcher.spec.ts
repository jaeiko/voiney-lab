import { test, expect } from '@playwright/test';

test.describe('Researcher / Bench workspace', () => {
  test('loads with the researcher workspace active and core state visible', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#researcher-workspace')).toBeVisible();
    await expect(page.locator('#workspace-researcher')).toHaveAttribute('aria-current', 'page');

    // Voice controls
    await expect(page.locator('#start')).toBeVisible();
    await expect(page.locator('#stop')).toBeDisabled();

    // Protocol selection
    await expect(page.locator('#protocol-id')).toBeVisible();

    // Experiment ledger / timeline empty state before any session exists
    await expect(page.locator('#experiment-session-ledger')).toBeVisible();
    await expect(page.locator('#experiment-event-timeline')).toBeVisible();
  });

  test('reviewer and admin nav items become visible for the default dev identity', async ({ page }) => {
    await page.goto('/');
    // loadWorkspaceSession() resolves asynchronously after page load.
    await expect(page.locator('#workspace-reviewer')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('#workspace-admin')).toBeVisible({ timeout: 10_000 });
  });

  test('experiment session status badge is hidden when no experiment is selected', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#experiment-session-status-badge')).toBeHidden();
  });

  test('evidence and observation capture controls are present with non-blank guidance text', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#manual-observation-content')).toBeVisible();
    await expect(page.locator('#experiment-evidence-file')).toBeVisible();
    const captureStatus = page.locator('#experiment-capture-status');
    await expect(captureStatus).not.toHaveText('');
  });

  test('long conversation stays contained beside the continuous step and timeline workspace', async ({ page }) => {
    await page.goto('/');
    await page.locator('#hero-setup').evaluate((node) => node.classList.add('collapsed'));
    await page.evaluate(() => {
      for (let turnId = 1; turnId <= 18; turnId += 1) {
        const node = turnNode(turnId);
        node.querySelector('.transcript').textContent = `연구자 음성 요청 ${turnId}`;
        node.querySelector('.reply').textContent = `서버 소유 상태 응답 ${turnId}`;
      }
    });
    const layout = await page.evaluate(() => {
      const procedure = document.querySelector('.procedure-card').getBoundingClientRect();
      const ledger = document.querySelector('#experiment-session-ledger').getBoundingClientRect();
      const chat = document.querySelector('.timeline').getBoundingClientRect();
      const log = document.querySelector('#log');
      return {
        workflowGap: ledger.top - procedure.bottom,
        viewportWidth: window.innerWidth,
        chatLeft: chat.left,
        chatTop: chat.top,
        workflowRight: procedure.right,
        ledgerBottom: ledger.bottom,
        logClientHeight: log.clientHeight,
        logScrollHeight: log.scrollHeight,
        horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    });
    expect(layout.workflowGap).toBeLessThanOrEqual(24);
    if (layout.viewportWidth > 980) {
      expect(layout.chatLeft).toBeGreaterThanOrEqual(layout.workflowRight - 1);
      expect(layout.logScrollHeight).toBeGreaterThan(layout.logClientHeight);
    } else {
      expect(layout.chatTop).toBeGreaterThanOrEqual(layout.ledgerBottom - 1);
    }
    expect(layout.horizontalOverflow).toBeLessThanOrEqual(1);
  });

  test('only explicitly selected open experiments use the resume action', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await page.evaluate(() => renderExperimentTimeline({
      session: {
        session_id: 'experiment-browser-resume', version: 7,
        status: 'in_progress', protocol_id: 'candidate-a',
        current_step_label: '2',
      },
      timeline: [],
    }));
    await expect(page.locator('#start')).toHaveText('실험 이어하기');
    await page.evaluate(() => renderExperimentTimeline({
      session: {
        session_id: 'experiment-browser-resume', version: 8,
        status: 'completed', protocol_id: 'candidate-a',
        current_step_label: '2',
      },
      timeline: [],
    }));
    await expect(page.locator('#start')).toHaveText('새 실험 시작');
  });
});
