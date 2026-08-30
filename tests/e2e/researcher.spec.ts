import { test, expect } from '@playwright/test';

test.describe.configure({ timeout: 45_000 });

/**
 * The bench page finishes its initial workspace load asynchronously and the last
 * step of that load re-renders the timeline. Wait for that settled state instead
 * of `networkidle`, which never settles when optional live-provider features are
 * enabled, and which would otherwise let an injected render be overwritten.
 */
async function benchReady(page) {
  await page.goto('/');
  await page.waitForFunction(() => typeof renderExperimentTimeline === 'function');
  await expect(page.locator('#experiment-event-timeline')).toContainText(
    '진행 중인 실험 없음',
    { timeout: 20_000 },
  );
}

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
    await expect(page.locator('#experiment-event-timeline')).toBeHidden();
    await page.locator('#experiment-session-ledger > summary').click();
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
    await page.locator('#experiment-session-ledger > summary').click();
    await expect(page.locator('#manual-observation-content')).toBeVisible();
    await expect(page.locator('#experiment-evidence-file')).toBeVisible();
    const captureStatus = page.locator('#experiment-capture-status');
    await expect(captureStatus).not.toHaveText('');
  });

  test('recorded evidence exposes a same-origin opaque download action', async ({ page }) => {
    await benchReady(page);
    await page.locator('#experiment-session-ledger > summary').click();
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
    await benchReady(page);
    await page.locator('#hero-setup').evaluate((node) => node.classList.add('collapsed'));
    await page.evaluate(() => {
      document.querySelector('#experiment-session-ledger').open = true;
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

  test('voice turns distinguish what was heard, understood, and saved', async ({ page }) => {
    await benchReady(page);
    await page.evaluate(() => {
      const configurationId = 71;
      const turnId = 9;
      const generation = 17;
      acceptedSessionConfiguration = {
        configuration_id: configurationId,
        mode: 'cascade', language: 'ko', protocol_id: 'candidate-a', revision_id: 'fixture-1',
      };
      const card = turnNode(turnId, sessionGeneration);
      turnServerGenerations.set(turnBrowserKey(turnId, sessionGeneration), generation);
      card.querySelector('.transcript').textContent = '다음 단계 알려줘';
      applyRouteDecision({
        configuration_id: configurationId, turn_id: turnId, generation,
        intent: 'current_step', runtime_router: 'curated_protocol',
        action: 'next_information', answer_origin: 'current_protocol', state_mutation: false,
      }, sessionGeneration);
    });

    // Locate by the card's own heading rather than by position: the log may
    // render newest-first, and the two cards below must not be confused.
    const turn = page.locator('.turn', { hasText: '요청 9' });
    await expect(turn.locator('.transcript')).toHaveText('다음 단계 알려줘');
    await expect(turn.locator('.understood')).toHaveText('다음 단계 미리 보기');
    await expect(turn.locator('.saved')).toHaveText('상태 변경 없음');
    await expect(turn.locator('.saved')).toHaveAttribute('data-mutated', 'false');
    // A question changed nothing, so no playback-outcome row appears either.
    await expect(turn.locator('.playback-outcome')).toBeHidden();

    // A completion is only reported as saved once the server says it persisted.
    await page.evaluate(() => {
      const configurationId = 71;
      const turnId = 10;
      const generation = 17;
      const card = turnNode(turnId, sessionGeneration);
      turnServerGenerations.set(turnBrowserKey(turnId, sessionGeneration), generation);
      card.querySelector('.transcript').textContent = '완료됐어요';
      applyRouteDecision({
        configuration_id: configurationId, turn_id: turnId, generation,
        intent: 'workflow_control', runtime_router: 'curated_protocol',
        action: 'advance_step', answer_origin: 'server_workflow_state',
        state_mutation: true,
      }, sessionGeneration);
    });
    const completion = page.locator('.turn', { hasText: '요청 10' });
    await expect(completion.locator('.saved')).toHaveText('처리 중…');
    await page.evaluate(() => applyTurnOutcome({
      configuration_id: 71, turn_id: 10, generation: 17,
      workflow_outcome: 'experiment_report_saved', state_changed: true,
      report_persisted: true, workflow_state_persisted: true,
    }, sessionGeneration));
    await expect(completion.locator('.saved')).toHaveText('실험 기록 저장됨');

    // Interrupting playback stops audio and says so, in its own row. The
    // committed workflow result above it is untouched.
    await page.evaluate(() => applyPlaybackOutcome(10, sessionGeneration, 'interrupted_by_user'));
    await expect(completion.locator('.playback-outcome')).toBeVisible();
    await expect(completion.locator('.playback-outcome')).toContainText('답변 재생만 중단됨');
    await expect(completion.locator('.saved')).toHaveText('실험 기록 저장됨');
  });

  test('Korean next-step presentation keeps the exact English source collapsed', async ({ page }) => {
    await benchReady(page);
    await page.evaluate(() => {
      const card = turnNode(22, sessionGeneration);
      card.querySelector('.transcript').textContent = '다음 단계 알려줘';
      card.querySelector('.understood').textContent = '다음 단계 미리 보기';
      card.querySelector('.saved').textContent = '상태 변경 없음';
      renderStructuredReply(card, {
        type: 'reply.complete',
        translation_status: 'automatic_translation',
        primary_text: '세척 용액 두 가지를 준비합니다. Solution A는 25 mM AMBIC입니다.',
        source_texts: ['Prepare two wash solutions. Solution A is 25 mM AMBIC.'],
        display_document: {
          title: '다음 2단계',
          sections: [
            { kind: 'lead', heading: '', text: '다음 단계는 2단계입니다.' },
            {
              kind: 'section', heading: '답변 · 자동 번역',
              text: '세척 용액 두 가지를 준비합니다. Solution A는 25 mM AMBIC입니다.',
            },
            {
              kind: 'source', heading: '원문 보기',
              text: 'Prepare two wash solutions. Solution A is 25 mM AMBIC.',
            },
            {
              kind: 'section', heading: '상태 확인',
              text: '현재 단계는 1단계이며 실험 상태는 변경하지 않았습니다.',
            },
          ],
        },
      });
    });

    const turn = page.locator('.turn', { hasText: '요청 22' });
    await expect(turn).toContainText('답변 · 자동 번역');
    await expect(turn).toContainText('세척 용액 두 가지를 준비합니다');
    await expect(turn.locator('.saved')).toHaveText('상태 변경 없음');
    const original = turn.locator('details.reference-details', { hasText: '원문 보기' });
    await expect(original).not.toHaveAttribute('open', '');
    await expect(original.locator('summary')).toHaveText('원문 보기');
    await expect(original.locator('.evidence-detail-body')).toBeHidden();
  });

  test('long-answer barge-in outcomes keep voice status rows compact', async ({ page }) => {
    await benchReady(page);
    await page.evaluate(() => {
      const turnId = 23;
      const card = turnNode(turnId, sessionGeneration);
      card.querySelector('.transcript').textContent = '다음 단계 알려줘';
      card.querySelector('.understood').textContent = '다음 단계 미리 보기';
      card.querySelector('.saved').textContent = '상태 변경 없음';
      card.querySelector('.reply').textContent = Array.from(
        { length: 18 },
        (_, index) => `자동 번역된 다음 단계 안내 ${index + 1}: 25 mM 조건을 원문과 함께 확인합니다.`,
      ).join(' ');
      const filler = card.querySelector('.filler-status');
      filler.textContent = '음성 안내 · 재생됨';
      filler.hidden = false;
      applyPlaybackOutcome(turnId, sessionGeneration, 'interrupted_by_user');
    });

    const turn = page.locator('.turn', { hasText: '요청 23' });
    const filler = turn.locator('.filler-status');
    const playback = turn.locator('.playback-outcome');
    await expect(filler).toHaveText('음성 안내 · 재생됨');
    await expect(playback).toContainText('답변 재생만 중단됨');

    const layout = await turn.evaluate((node) => {
      const reply = node.querySelector('.reply').getBoundingClientRect();
      const fillerStatus = node.querySelector('.filler-status').getBoundingClientRect();
      const playbackStatus = node.querySelector('.playback-outcome').getBoundingClientRect();
      return {
        replyHeight: reply.height,
        fillerHeight: fillerStatus.height,
        playbackHeight: playbackStatus.height,
        horizontalOverflow: node.scrollWidth - node.clientWidth,
      };
    });
    expect(layout.replyHeight).toBeGreaterThan(80);
    expect(layout.fillerHeight).toBeLessThan(layout.replyHeight / 2);
    expect(layout.fillerHeight).toBeLessThanOrEqual(56);
    expect(layout.playbackHeight).toBeLessThanOrEqual(56);
    expect(layout.horizontalOverflow).toBeLessThanOrEqual(1);
  });

  test('a human checkpoint is shown as bench work with two explicit answers', async ({ page }) => {
    await benchReady(page);
    await expect(page.locator('#human-checkpoint')).toBeHidden();

    await page.evaluate(() => renderHumanCheckpoint({
      active: true,
      human_checkpoint: {
        checkpoint_id: 'candidate-a-repeat-steps-02-07',
        gate_step_id: 'candidate-a-step-07', gate_step_label: '7',
        condition_source_text: '7 Repeat steps 2-7 until the gel band is fully destained.',
        condition_primary_text: '젤 밴드가 완전히 탈색될 때까지 2–7단계를 반복합니다.',
        source_page: 5,
        repeated_step_ids: ['candidate-a-step-02', 'candidate-a-step-07'],
        repeated_step_labels: ['2', '3', '4', '5', '6', '7'],
        confirmed_repetitions: 1,
        repetition_review_threshold: 5,
        awaiting_continuation_decision: false,
        authority: 'researcher_observation',
      },
    }));

    const card = page.locator('#human-checkpoint');
    await expect(card).toBeVisible();
    await expect(card).toContainText('연구자가 직접 확인');
    await expect(page.locator('#human-checkpoint-source-text')).toContainText('fully destained');
    await expect(page.locator('#human-checkpoint-primary')).toContainText('탈색');
    await expect(page.locator('#human-checkpoint-page')).toContainText('p.5');
    await expect(page.locator('#human-checkpoint-repeat')).toContainText('2–7단계');
    await expect(page.locator('#human-checkpoint-not-met')).toHaveText('아직 충족되지 않음');
    await expect(page.locator('#human-checkpoint-met')).toHaveText('조건 충족 확인');
    await expect(page.locator('#human-checkpoint-continuation')).toBeHidden();
    // It is a checkpoint, not an error: no alert styling, no blocker wording.
    await expect(card).not.toContainText('오류');
    await expect(card).not.toContainText('unsupported');
  });

  test('the repetition check-in asks the researcher instead of inventing a maximum', async ({ page }) => {
    await benchReady(page);
    await page.evaluate(() => renderHumanCheckpoint({
      active: true,
      human_checkpoint: {
        checkpoint_id: 'candidate-a-repeat-steps-02-07',
        gate_step_id: 'candidate-a-step-07', gate_step_label: '7',
        condition_source_text: '7 Repeat steps 2-7 until the gel band is fully destained.',
        condition_primary_text: null, source_page: 5,
        repeated_step_ids: ['candidate-a-step-02'], repeated_step_labels: ['2', '7'],
        confirmed_repetitions: 5, repetition_review_threshold: 5,
        awaiting_continuation_decision: true, authority: 'researcher_observation',
      },
    }));
    await expect(page.locator('#human-checkpoint-continuation')).toBeVisible();
    await expect(page.locator('#human-checkpoint-continuation-text')).toContainText('원문에는 최대 반복 횟수가 정해져 있지 않습니다');
    await expect(page.locator('#human-checkpoint-continue')).toBeVisible();
    await expect(page.locator('#human-checkpoint-pause')).toBeVisible();
    await expect(page.locator('#human-checkpoint-review')).toBeVisible();
  });

  test('only explicitly selected open experiments use the resume action', async ({ page }) => {
    await benchReady(page);
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

  test('reports microphone state and Turn outcomes in bench Korean, not internals', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#researcher-workspace')).toBeVisible();

    // A bench user needs a readable microphone state, and it must not be a
    // number: raw VAD probabilities belong in the developer detail panel.
    const microphone = page.locator('#microphone-status');
    await expect(microphone).toBeVisible();
    await expect(microphone).not.toHaveText('');
    expect(await microphone.innerText()).not.toMatch(/\d+(\.\d+)?\s*(rms|dB|%)/i);
    // Tone is carried by an attribute and a glyph, not by colour alone.
    await expect(microphone).toHaveAttribute('data-tone', /idle|ok|warn|error/);

    // Korean is the primary vocabulary of the voice history.
    const timeline = page.locator('.panel.timeline');
    await expect(timeline).toContainText('들은 말');
    await expect(timeline).toContainText('이해한 내용');
    await expect(timeline).toContainText('처리 결과');
    expect(await timeline.innerText()).not.toContain('HEARD');
    expect(await timeline.innerText()).not.toContain('SAVED');
  });

  test('supports reduced motion, visible keyboard focus, modal focus return, and assistive announcements', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/');
    const motion = await page.locator('.pulse').evaluate((node) => {
      const style = getComputedStyle(node);
      return { animation: style.animationDuration, transition: style.transitionDuration };
    });
    expect(parseFloat(motion.animation)).toBeLessThanOrEqual(0.00001);
    expect(parseFloat(motion.transition)).toBeLessThanOrEqual(0.00001);

    const newSession = page.locator('#rail-new-session');
    await newSession.focus();
    const focus = await newSession.evaluate((node) => {
      const style = getComputedStyle(node);
      return { style: style.outlineStyle, width: style.outlineWidth };
    });
    expect(focus.style).not.toBe('none');
    expect(parseFloat(focus.width)).toBeGreaterThanOrEqual(2);

    await page.evaluate(() => { sessionActive = true; });
    await newSession.click();
    await expect(page.locator('#new-session-modal')).toBeVisible();
    await expect(page.locator('#modal-cancel-btn')).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(page.locator('#new-session-modal')).toBeHidden();
    await expect(newSession).toBeFocused();

    const announcer = page.locator('#screen-reader-announcer');
    await page.evaluate(() => announceMilestone('상태 변경이 차단되었습니다.'));
    await expect(announcer).toHaveText('상태 변경이 차단되었습니다.');
    await page.evaluate(() => announceMilestone('현재 단계 완료를 저장했습니다.'));
    await expect(announcer).toHaveText('현재 단계 완료를 저장했습니다.');
  });

  test('stays within the viewport at a narrow tablet width', async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.goto('/');
    await expect(page.locator('#researcher-workspace')).toBeVisible();
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth);
    // A horizontal scrollbar on the whole page makes a gloved touch target
    // drift under the user's finger; wide content must scroll inside its own
    // container instead.
    expect(overflow).toBeLessThanOrEqual(1);

    // The primary action stays reachable and comfortably tappable.
    const start = page.locator('#start');
    await expect(start).toBeVisible();
    const box = await start.boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(32);

    await page.evaluate(() => {
      const card = turnNode(24, sessionGeneration);
      card.querySelector('.reply').textContent = '긴 다음 단계 안내 '.repeat(120);
      const filler = card.querySelector('.filler-status');
      filler.textContent = '음성 안내 · 재생됨';
      filler.hidden = false;
      applyPlaybackOutcome(24, sessionGeneration, 'interrupted_by_user');
    });
    const statusTurn = page.locator('.turn', { hasText: '요청 24' });
    const statusLayout = await statusTurn.evaluate((node) => ({
      filler: node.querySelector('.filler-status').getBoundingClientRect().height,
      playback: node.querySelector('.playback-outcome').getBoundingClientRect().height,
      overflow: node.scrollWidth - node.clientWidth,
    }));
    expect(statusLayout.filler).toBeLessThanOrEqual(56);
    expect(statusLayout.playback).toBeLessThanOrEqual(56);
    expect(statusLayout.overflow).toBeLessThanOrEqual(1);
  });
});
