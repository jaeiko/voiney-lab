import { test, expect, type Page, TAB } from './routes';

test.describe.configure({ timeout: 45_000 });

// The page's own startup ends in loadExperimentSessions() ->
// loadExperimentTimeline(null), which clears the timeline and resets the
// start action and the step. A test that renders a timeline itself has to
// do it after that, or the late reset overwrites its render (on CI it landed
// 15-90 ms after the test's render). waitForResponse settles on the headers,
// before the page has read the body, so this also waits for the reset's own
// row: the first timeline row with a <b>, which the initial row lacks.
// Call it before page.goto() so the response cannot be missed.
async function experimentSessionsLoaded(page: Page) {
  await page.waitForResponse(
    response => new URL(response.url()).pathname === '/api/workspace/experiments',
  );
  await page
    .locator('#experiment-event-timeline .workspace-row b')
    .first()
    .waitFor({ state: 'attached' });
}

test.describe('Researcher / Bench workspace', () => {
  // Lane IA (2026-10-19): the one page became five tabs; each part is checked on its own tab.
  test('loads with the home active and each tab showing its own part', async ({ page }) => {
    await page.goto(TAB.home);
    await expect(page.locator('#researcher-workspace')).toBeVisible();
    await expect(page.locator('#tab-bar')).toBeVisible();
    await expect(page.locator('#home-cards .home-card').first()).toBeVisible();

    // Voice controls on the experiment tab (lane ON's wizard stands over them before a run)
    await page.locator('#tab-link-experiment').click();
    await expect(page.locator('#start-wizard')).toBeVisible();
    await expect(page.locator('#stop')).toBeDisabled();
    await expect(page.locator('#state')).toHaveText('준비');

    // Protocol selection on the settings tab; the context card's words are filled
    await page.locator('#tab-link-settings').click();
    await expect(page.locator('#protocol-id')).toBeVisible();
    await expect(page.locator('#experiment-context-name')).not.toHaveText('');
    await expect(page.locator('#experiment-context-version')).not.toHaveText('');
    await expect(page.locator('#experiment-context-experimenter')).not.toHaveText('');
    await expect(page.locator('#experiment-context-actions')).not.toHaveText('');

    // Experiment ledger / timeline empty state on the records tab before any session exists
    await page.locator('#tab-link-records').click();
    await expect(page.locator('#experiment-session-ledger')).toBeVisible();
    await expect(page.locator('#experiment-event-timeline')).toBeVisible();
  });

  test('experiment session status badge is hidden when no experiment is selected', async ({ page }) => {
    await page.goto(TAB.records);
    await expect(page.locator('#experiment-session-status-badge')).toBeHidden();
  });

  test('evidence and observation capture controls are present with non-blank guidance text', async ({ page }) => {
    await page.goto(TAB.records);
    await expect(page.locator('#manual-observation-content')).toBeVisible();
    await expect(page.locator('#experiment-evidence-file')).toBeVisible();
    const captureStatus = page.locator('#experiment-capture-status');
    await expect(captureStatus).not.toHaveText('');
  });

  test('recorded evidence exposes a same-origin opaque download action', async ({ page }) => {
    const startupDone = experimentSessionsLoaded(page);
    await page.goto(TAB.records);
    await startupDone;
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

  test('a long timeline stays contained on the records tab', async ({ page }) => {
    // Lane FX3 decision 1: this test renders its own 24-row timeline, so it
    // waits for the startup reset first (it flaked 3 of 5 CI runs when the
    // reset landed after the render: event list scroll 74 = client 74).
    // Lane IA: the conversation lives on the experiment tab now (a sheet during
    // the run, beside the record after it), so only the ledger is measured here.
    const startupDone = experimentSessionsLoaded(page);
    await page.goto(TAB.records);
    await startupDone;
    await page.waitForFunction(() => typeof renderExperimentTimeline === 'function');
    await page.evaluate(() => {
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
      const ledger = document.querySelector('#experiment-session-ledger').getBoundingClientRect();
      const capture = document.querySelector('.experiment-ledger-capture').getBoundingClientRect();
      const events = document.querySelector('#experiment-event-timeline');
      return {
        viewportWidth: window.innerWidth,
        ledgerVisible: ledger.width > 0 && ledger.height > 0,
        captureHeight: capture.height,
        eventClientHeight: events.clientHeight,
        eventScrollHeight: events.scrollHeight,
        horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    });
    expect(layout.ledgerVisible).toBe(true);
    if (layout.viewportWidth > 980) {
      expect(layout.eventScrollHeight).toBeGreaterThan(layout.eventClientHeight);
      expect(layout.eventClientHeight).toBeLessThan(layout.captureHeight);
    } else {
      expect(layout.eventScrollHeight).toBeLessThanOrEqual(layout.eventClientHeight + 1);
    }
    expect(layout.horizontalOverflow).toBeLessThanOrEqual(1);
  });

  test('only explicitly selected open experiments use the resume action', async ({ page }) => {
    const startupDone = experimentSessionsLoaded(page);
    await page.goto(TAB.records);
    await startupDone;
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
    // Lane U decision 2: the revision id is folded into the developer details.
    await expect(page.locator('#experiment-context-version')).toContainText('저장된 실험의 버전');
    await expect(page.locator('#experiment-context-version .dev-code-details')).toContainText('pdf-1-analysis-4');
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
