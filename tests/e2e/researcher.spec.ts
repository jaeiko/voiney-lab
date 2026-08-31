import { test, expect } from '@playwright/test';

test.describe.configure({ timeout: 45_000 });

test.describe('Researcher / Bench workspace', () => {
  test('loads with the researcher workspace active and core state visible', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#researcher-workspace')).toBeVisible();
    await expect(page.locator('#workspace-researcher')).toHaveAttribute('aria-current', 'page');

    // Voice controls
    await expect(page.locator('#start')).toBeVisible();
    await expect(page.locator('#stop')).toBeDisabled();
    await expect(page.locator('#state')).toHaveText('준비');

    // Protocol selection
    await expect(page.locator('#protocol-id')).toBeVisible();
    await expect(page.locator('.experiment-context-card')).toBeVisible();
    await expect(page.locator('#experiment-context-name')).not.toHaveText('');
    await expect(page.locator('#experiment-context-version')).not.toHaveText('');
    await expect(page.locator('#experiment-context-approval')).not.toHaveText('');
    await expect(page.locator('#experiment-context-actions')).not.toHaveText('');

    // Experiment ledger / timeline empty state before any session exists
    await expect(page.locator('#experiment-session-ledger')).toBeVisible();
    await expect(page.locator('#experiment-event-timeline')).toBeVisible();
  });

  test('reviewer and admin nav items become visible for the default dev identity', async ({ page }) => {
    await page.goto('/');
    // loadWorkspaceSession() resolves asynchronously after page load.
    await expect(page.locator('#workspace-reviewer')).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('#workspace-admin')).toBeVisible({ timeout: 20_000 });
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

  test('recorded evidence exposes a same-origin opaque download action', async ({ page }) => {
    await page.goto('/');
    // This test exercises the local renderer. A large catalog refresh may still
    // be in flight and is not a prerequisite for rendering an evidence event.
    await page.waitForFunction(() => typeof renderExperimentTimeline === 'function');
    await page.evaluate(() => renderExperimentTimeline({
      session: {
        session_id: 'experiment-evidence-browser', version: 2,
        status: 'in_progress', protocol_id: 'candidate-a',
        protocol_revision_id: 'pdf-1-analysis-4',
        current_step_id: 'step-1', current_step_label: '1',
      },
      timeline: [{
        event_type: 'evidence_attached', step_label: '1',
        created_at: '2026-08-24T00:00:00+00:00',
        evidence: {
          evidence_id: 'evidence-browser-1', original_filename: 'private-name.jpg',
          media_type: 'image/jpeg', interpretation_status: 'not_interpreted',
        },
      }],
      observation_count: 0, evidence_count: 1,
    }));
    const link = page.locator('.experiment-evidence-download');
    await expect(link).toHaveText('원본 증거 다운로드');
    await expect(link).toHaveAttribute(
      'href',
      '/api/workspace/experiments/experiment-evidence-browser/evidence/evidence-browser-1',
    );
    await expect(link).not.toHaveAttribute('href', /private-name/);
  });

  test('long conversation stays contained beside the continuous step and timeline workspace', async ({ page }) => {
    await page.goto('/');
    await page.waitForFunction(() => typeof renderExperimentTimeline === 'function');
    await page.locator('#hero-setup').evaluate((node) => node.classList.add('collapsed'));
    await page.evaluate(() => {
      for (let turnId = 1; turnId <= 18; turnId += 1) {
        const node = turnNode(turnId);
        node.querySelector('.transcript').textContent = `연구자 음성 요청 ${turnId}`;
        node.querySelector('.reply').textContent = `서버 소유 상태 응답 ${turnId}`;
      }
      renderExperimentTimeline({
        session: {
          session_id: 'experiment-layout-audit', version: 24,
          status: 'in_progress', protocol_id: 'candidate-a',
          protocol_revision_id: 'pdf-1-analysis-4',
          current_step_id: 'step-24', current_step_label: '24',
        },
        timeline: Array.from({ length: 24 }, (_, index) => ({
          event_type: index % 3 === 0 ? 'observation_recorded' : 'step_completed',
          step_label: String(index + 1),
          created_at: `2026-08-24T${String(index).padStart(2, '0')}:00:00Z`,
          observation: index % 3 === 0 ? {
            category: 'note', content: `관찰 기록 ${index + 1} · 승인된 지침을 변경하지 않음`,
          } : undefined,
        })),
        observation_count: 8, evidence_count: 0,
      });
    });
    const layout = await page.evaluate(() => {
      const procedure = document.querySelector('.procedure-card').getBoundingClientRect();
      const ledger = document.querySelector('#experiment-session-ledger').getBoundingClientRect();
      const chat = document.querySelector('.timeline').getBoundingClientRect();
      const log = document.querySelector('#log');
      const capture = document.querySelector('.experiment-ledger-capture').getBoundingClientRect();
      const events = document.querySelector('#experiment-event-timeline');
      return {
        workflowGap: ledger.top - procedure.bottom,
        viewportWidth: window.innerWidth,
        chatLeft: chat.left,
        chatTop: chat.top,
        workflowRight: procedure.right,
        ledgerBottom: ledger.bottom,
        logClientHeight: log.clientHeight,
        logScrollHeight: log.scrollHeight,
        captureHeight: capture.height,
        eventClientHeight: events.clientHeight,
        eventScrollHeight: events.scrollHeight,
        horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    });
    expect(layout.workflowGap).toBeLessThanOrEqual(24);
    if (layout.viewportWidth > 980) {
      expect(layout.chatLeft).toBeGreaterThanOrEqual(layout.workflowRight - 1);
      expect(layout.logScrollHeight).toBeGreaterThan(layout.logClientHeight);
      expect(layout.eventScrollHeight).toBeGreaterThan(layout.eventClientHeight);
      expect(layout.eventClientHeight).toBeLessThan(layout.captureHeight);
    } else {
      expect(layout.chatTop).toBeGreaterThanOrEqual(layout.ledgerBottom - 1);
      expect(layout.eventScrollHeight).toBeLessThanOrEqual(layout.eventClientHeight + 1);
    }
    expect(layout.horizontalOverflow).toBeLessThanOrEqual(1);
  });

  test('only explicitly selected open experiments use the resume action', async ({ page }) => {
    await page.goto('/');
    await page.waitForFunction(() => typeof renderExperimentTimeline === 'function');
    await page.evaluate(() => renderExperimentTimeline({
      session: {
        session_id: 'experiment-browser-resume', version: 7,
        status: 'in_progress', protocol_id: 'candidate-a',
        protocol_revision_id: 'pdf-1-analysis-4',
        current_step_id: 'step-2', current_step_label: '2',
      },
      timeline: [],
      recovery: {
        eligible: true,
        last_event_type: 'session_recovered',
        restored: {
          protocol_id: 'candidate-a',
          protocol_revision_id: 'pdf-1-analysis-4',
          current_step_id: 'step-2', current_step_label: '2',
          completed_step_count: 1,
        },
        not_restored: ['pending_confirmations', 'conversation_history', 'active_timers'],
        next_action: 'resume_voice_session',
      },
    }));
    await expect(page.locator('#start')).toHaveText('실험 이어하기');
    await expect(page.locator('#experiment-context-version')).toHaveText('pdf-1-analysis-4');
    await expect(page.locator('#experiment-context-step')).toContainText('2단계');
    await expect(page.locator('#experiment-resume-disclosure')).toContainText('서버 복구 확인 완료');
    await expect(page.locator('#experiment-resume-disclosure')).toContainText('완료 확인 대기');
    await expect(page.locator('#experiment-resume-disclosure')).toContainText('이전 대화');
    await expect(page.locator('#experiment-resume-disclosure')).toContainText('진행 중이던 단계 타이머');
    await page.evaluate(() => renderExperimentTimeline({
      session: {
        session_id: 'experiment-browser-resume', version: 8,
        status: 'completed', protocol_id: 'candidate-a',
        protocol_revision_id: 'pdf-1-analysis-4',
        current_step_id: 'step-2', current_step_label: '2',
      },
      timeline: [],
      recovery: {
        eligible: false,
        last_event_type: 'session_recovered',
        restored: {
          protocol_id: 'candidate-a',
          protocol_revision_id: 'pdf-1-analysis-4',
          current_step_id: 'step-2', current_step_label: '2',
          completed_step_count: 1,
        },
        not_restored: ['pending_confirmations', 'conversation_history', 'active_timers'],
        next_action: 'start_new_experiment',
      },
    }));
    await expect(page.locator('#start')).toHaveText('새 실험 시작');
    await expect(page.locator('#experiment-resume-disclosure')).toContainText('종료되어');
  });
});
