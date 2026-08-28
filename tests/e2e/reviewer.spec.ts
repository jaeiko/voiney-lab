import { test, expect } from '@playwright/test';

test.describe.configure({ timeout: 45_000 });

async function openReviewerWorkspace(page) {
  await page.goto('/');
  await page.locator('#workspace-reviewer').waitFor({ state: 'visible', timeout: 20_000 });
  await page.locator('#workspace-reviewer').click();
  await expect(page.locator('#reviewer-workspace')).toBeVisible();
  await expect(page.locator('#researcher-workspace')).toBeHidden();
  await expect(page.locator('#admin-workspace')).toBeHidden();
}

test.describe('Reviewer workspace', () => {
  test('clicking 검토하기 loads the selected reviewer packet', async ({ page }) => {
    await page.route('**/api/protocols/review-queue', async route => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({ protocols: [{
        protocol_id: 'click-review-protocol', title: 'Click Review Protocol',
        source_filename: 'click-review.pdf', step_count: 3,
        needs_resolution_count: 0, human_checkpoint_count: 1,
        execution_readiness: { display_label: '실행 승인 가능' },
      }] }),
    }));
    await page.route('**/api/protocols/click-review-protocol/review', async route => route.fulfill({
      status: 200, contentType: 'application/json', body: JSON.stringify({
        protocol_id: 'click-review-protocol', title: 'Click Review Protocol',
        revision_id: 'pdf-1-analysis-1', step_count: 3,
        source: { filename: 'click-review.pdf', page_count: 2 }, sections: [],
        needs_resolution: [], human_checkpoints: [{
          checkpoint_id: 'checkpoint-click', gate_step_label: '3',
          condition_source_text: 'Repeat until visibly clear.',
          source_page_number: 2, repeated_step_labels: ['2', '3'],
        }],
        execution_readiness: {
          state: 'ready_for_execution_approval', display_label: '실행 승인 가능',
          display_detail: '실행을 막는 항목이 없습니다.',
          can_approve_for_execution: true, can_revoke_execution_approval: false,
          blockers: [],
        },
      }),
    }));
    await openReviewerWorkspace(page);
    await page.locator('#reviewer-protocol-queue button', { hasText: '검토하기' }).click();
    await expect(page.locator('#reviewer-selection')).toContainText('Click Review Protocol');
    await expect(page.locator('#reviewer-checkpoints')).toContainText('Repeat until visibly clear');
    await expect(page.locator('#reviewer-readiness')).toContainText('실행 승인 가능');
  });

  for (const statusCode of [403, 500]) {
    test(`review queue HTTP ${statusCode} is visibly an error, never an empty queue`, async ({ page }) => {
      await page.route('**/api/protocols/review-queue', async route => route.fulfill({
        status: statusCode, contentType: 'application/json', body: '{}',
      }));
      await openReviewerWorkspace(page);
      await expect(page.locator('#reviewer-protocol-queue')).toContainText('목록을 확인하지 못했으며');
      await expect(page.locator('#reviewer-protocol-queue')).not.toContainText('필요한 프로토콜이 없습니다');
    });
  }

  test('leads with the queue, unresolved items, and a decision that says what it does', async ({ page }) => {
    await openReviewerWorkspace(page);
    await expect(page.locator('#reviewer-protocol-queue')).toBeVisible();
    await expect(page.locator('#reviewer-inbox')).toBeVisible();

    // Unresolved work and researcher checkpoints are the default view.
    await expect(page.locator('#reviewer-attention')).toBeVisible();
    await expect(page.locator('#reviewer-checkpoints')).toBeVisible();
    await expect(page.locator('#reviewer-workspace')).toContainText(
      '실험 중 연구자가 직접 확인하는 조건입니다',
    );

    // Source evidence, change detail and history stay behind disclosure.
    await expect(page.locator('#reviewer-change-summary')).toBeHidden();
    await expect(page.locator('#reviewer-diff')).toBeHidden();
    await page.locator('#reviewer-evidence-details').click();
    await expect(page.locator('#reviewer-change-summary')).toBeVisible();
    await page.locator('#reviewer-technical-details').click();
    await expect(page.locator('#reviewer-diff')).toBeVisible();
    await expect(page.locator('#reviewer-history')).toBeVisible();

    await expect(page.locator('#reviewer-approve')).toHaveText('연구자 사용 승인');
    await expect(page.locator('#reviewer-reject')).toHaveText('수정 요청');
    await expect(page.locator('#reviewer-revoke')).toHaveText('사용 중지');
    // A first-time reviewer must be told what the page is for and what
    // approving actually releases, without opening any disclosure.
    await expect(page.locator('#reviewer-workspace .workspace-purpose')).toContainText('연구자가 사용하기 전에');
    await expect(page.locator('#reviewer-consequence-title')).toHaveText('승인하면 어떻게 되나요?');
  });

  test('renders a human checkpoint as informational and a true ambiguity as actionable', async ({ page }) => {
    await openReviewerWorkspace(page);
    await page.evaluate(() => {
      renderProtocolReviewPacket({
        title: 'In-gel digestion', revision_id: 'pdf-1-analysis-1', step_count: 25,
        source: { filename: 'in-gel-digestion.pdf', page_count: 12 },
        execution_readiness: {
          state: 'needs_clarification', display_label: '원문 해석 확인 필요',
          display_detail: '검토자가 원문의 의미를 확정해야 합니다.',
          can_approve_for_execution: false, can_revoke_execution_approval: false,
          blockers: [{ code: 'unresolved_ambiguity', display_label: '원문 해석 확인 필요', display_detail: '의도가 한 가지로 읽히지 않습니다.', reviewer_resolvable: true }],
        },
        human_checkpoints: [{
          checkpoint_id: 'repeat-2-7', gate_step_label: '7', source_page_number: 5,
          condition_source_text: 'Repeat steps 2-7 until the gel band is fully destained.',
          repeated_step_labels: ['2', '3', '4', '5', '6', '7'], blocks_execution: false,
        }],
        needs_resolution: [{
          issue_id: 'step-20-range', display_label: '원문 해석 확인 필요',
          source_excerpt: 'repeat steps 1718 until fully dehydrated',
          source_page_number: 8, step_label: '20', accepts_repeat_range: true,
          selectable_steps: [{ step_id: 's17', source_label: '17' }, { step_id: 's18', source_label: '18' }],
        }],
        sections: [],
      });
    });

    await expect(page.locator('#reviewer-checkpoints')).toContainText('연구자 확인');
    await expect(page.locator('#reviewer-checkpoints')).toContainText('fully destained');
    await expect(page.locator('#reviewer-attention')).toContainText('원문 해석 확인 필요');
    await expect(page.locator('#reviewer-attention')).toContainText('p.8');
    await expect(page.locator('#reviewer-approve')).toBeDisabled();
    await expect(page.locator('#reviewer-action-guidance')).toContainText('확인이 필요한 항목');

    await page.locator('#reviewer-attention button', { hasText: '해석 확인하기' }).click();
    await expect(page.locator('#reviewer-resolution-form')).toBeVisible();
    await expect(page.locator('#reviewer-resolution-excerpt')).toContainText('1718');
    await expect(page.locator('#reviewer-resolution-range option')).toHaveCount(2);

    await page.evaluate(() => {
      renderProtocolReviewPacket({
        title: 'In-gel digestion', revision_id: 'pdf-1-analysis-2', step_count: 25,
        source: { filename: 'in-gel-digestion.pdf', page_count: 12 },
        execution_readiness: {
          state: 'ready_for_execution_approval', display_label: '실행 승인 가능',
          display_detail: '실행을 막는 항목이 없습니다.',
          can_approve_for_execution: true, can_revoke_execution_approval: false, blockers: [],
        },
        human_checkpoints: [], needs_resolution: [], sections: [],
      });
    });
    await expect(page.locator('#reviewer-readiness')).toContainText('실행 승인 가능');
    await expect(page.locator('#reviewer-approve')).toBeEnabled();
    await expect(page.locator('#reviewer-attention')).toContainText('확인이 필요한 항목이 없습니다');
  });

  test('stages an allowed decision with explicit consequences and does not imply unknown risk', async ({ page }) => {
    await openReviewerWorkspace(page);
    await page.evaluate(() => {
      selectedWorkspaceRevision = 'revision-browser-review';
      renderReviewerPacket({
        review_context: {
          protocol_title: 'ANKOM Fiber Analysis',
          revision_id: 'revision-browser-review', version_label: 'v2',
          requester_display_name: 'Researcher A',
          change_reason: 'Clarify the acid warning',
          source: { connector_kind: 'protocols_io', version_identity: 'source-v2' },
        },
        change_summary: {
          changed_fields: ['steps', 'warnings'],
          step_count_before: 2, step_count_after: 2,
          warning_count_before: 1, warning_count_after: 2,
          structured_adaptation_changes: [],
        },
        experimental_impact: { status: 'not_assessed' },
        risk: { level: 'not_assessed', source_signal: 'hazard_review' },
        decision_state: {
          state: 'review_required', allowed_actions: ['approved', 'rejected'],
        },
        history: [],
      });
    });
    await expect(page.locator('#reviewer-risk')).toContainText('위험 수준 판정이 아닙니다');
    await expect(page.locator('#reviewer-revoke')).toBeDisabled();
    await page.locator('#reviewer-comment').fill('Source and warning changes reviewed.');
    await page.locator('#reviewer-approve').click();
    await expect(page.locator('#reviewer-decision-confirmation')).toBeVisible();
    await expect(page.locator('#reviewer-confirm-consequence')).toContainText('새 실험');
    await page.locator('#reviewer-decision-cancel').click();
    await expect(page.locator('#reviewer-decision-confirmation')).toBeHidden();
    await expect(page.locator('#reviewer-status')).toContainText('프로토콜은 그대로입니다');
  });

  test('the approve action is visually distinct from OCR acceptance so the two can never be confused', async ({ page }) => {
    await openReviewerWorkspace(page);
    const approveClass = await page.locator('#reviewer-approve').getAttribute('class');
    expect(approveClass).toContain('btn-reviewer-approve');
    // The researcher-side OCR acceptance control lives in a different workspace
    // entirely (own protocol upload review), and carries its own neutral class.
    await page.locator('#workspace-researcher').click();
    const ocrAcceptClass = await page.locator('#protocol-ocr-accept').getAttribute('class');
    expect(ocrAcceptClass).toContain('btn-ocr-neutral');
    expect(ocrAcceptClass).not.toContain('btn-reviewer-approve');
  });

  test('source connector panels (protocols.io, Drive, GitHub) are present and read-only-labelled', async ({ page }) => {
    await openReviewerWorkspace(page);
    // Import panels are available but no longer compete with the review work.
    await expect(page.locator('#protocols-io-import')).toBeHidden();
    await page.locator('#reviewer-workspace details.reviewer-source-group').first().click();
    await expect(page.locator('#protocols-io-import')).toBeVisible();
    await expect(page.locator('#drive-sync')).toBeHidden();
    await expect(page.locator('#github-status')).toContainText('실행하지 않습니다');
  });

  test('a short queue leads into one aligned readable decision column', async ({ page }) => {
    await openReviewerWorkspace(page);
    await page.evaluate(() => {
      const inbox = document.querySelector('#reviewer-inbox');
      inbox.replaceChildren(workspaceRow(
        '신규 · Long Review Protocol · v18',
        'Researcher A · 변경 이유: clarify a long multi-step method · protocols.io · 위험 수준 확인 필요',
      ));
      renderReviewerPacket({
        review_context: {
          protocol_title: 'Long Review Protocol', revision_id: 'revision-layout-audit',
          version_label: 'v18', requester_display_name: 'Researcher A',
          change_reason: 'Clarify a long multi-step method',
          source: { connector_kind: 'protocols_io', version_identity: 'source-v18' },
        },
        change_summary: {
          changed_fields: ['steps', 'warnings', 'notes', 'materials', 'equipment'],
          step_count_before: 12, step_count_after: 14,
          warning_count_before: 2, warning_count_after: 5,
          structured_adaptation_changes: Array.from({ length: 12 }, (_, index) => ({
            kind: 'site_note', protocol_step_id: `step-${index + 1}`,
            summary: `시설별 명확화 ${index + 1}`, rationale: '검토 가능한 원문 근거 유지',
          })),
        },
        experimental_impact: { status: 'not_assessed' },
        risk: { level: 'not_assessed', source_signal: 'hazard_review' },
        decision_state: { state: 'review_required', allowed_actions: ['approved', 'rejected'] },
        history: Array.from({ length: 12 }, (_, index) => ({
          action: index % 2 ? 'rejected' : 'approved', affected_version: `v${index + 1}`,
          actor_display_name: `Reviewer ${index + 1}`, actor_role: 'reviewer',
          created_at: `2026-08-${String(index + 1).padStart(2, '0')}T10:00:00Z`,
          comment: 'Detailed decision record for layout validation',
        })),
      });
      const diff = document.querySelector('#reviewer-diff');
      diff.textContent = Array.from(
        { length: 90 },
        (_, index) => `Line ${index + 1}: exact scientific change remains source-linked and readable.`,
      ).join('\n');
      document.querySelector('#reviewer-evidence-details').open = true;
      document.querySelector('#reviewer-technical-details').open = true;
    });

    const layout = await page.evaluate(() => {
      const cards = document.querySelectorAll('#reviewer-workspace .workspace-columns > .workspace-card');
      const inboxCard = cards[0].getBoundingClientRect();
      const decisionCard = cards[1].getBoundingClientRect();
      const change = document.querySelector('#reviewer-change-summary');
      const history = document.querySelector('#reviewer-history');
      const diff = document.querySelector('#reviewer-diff');
      const hint = document.querySelector('#reviewer-history + .bounded-list-hint');
      return {
        viewportWidth: window.innerWidth,
        inboxCardHeight: inboxCard.height,
        decisionCardHeight: decisionCard.height,
        inboxCardWidth: inboxCard.width,
        decisionCardWidth: decisionCard.width,
        inboxCardBottom: inboxCard.bottom,
        decisionCardTop: decisionCard.top,
        changeClientHeight: change.clientHeight,
        changeScrollHeight: change.scrollHeight,
        historyClientHeight: history.clientHeight,
        historyScrollHeight: history.scrollHeight,
        diffClientHeight: diff.clientHeight,
        diffScrollHeight: diff.scrollHeight,
        hintDisplay: getComputedStyle(hint).display,
        horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    });
    expect(Math.abs(layout.inboxCardWidth - layout.decisionCardWidth)).toBeLessThanOrEqual(1);
    expect(layout.decisionCardTop).toBeGreaterThanOrEqual(layout.inboxCardBottom - 1);
    if (layout.viewportWidth > 980) {
      expect(layout.changeScrollHeight).toBeGreaterThan(layout.changeClientHeight);
      expect(layout.historyScrollHeight).toBeGreaterThan(layout.historyClientHeight);
      expect(layout.hintDisplay).not.toBe('none');
    } else {
      expect(layout.changeScrollHeight).toBeLessThanOrEqual(layout.changeClientHeight + 1);
      expect(layout.historyScrollHeight).toBeLessThanOrEqual(layout.historyClientHeight + 1);
      expect(layout.hintDisplay).toBe('none');
    }
    expect(layout.diffScrollHeight).toBeGreaterThan(layout.diffClientHeight);
    expect(layout.horizontalOverflow).toBeLessThanOrEqual(1);
    await expect(page.locator('#reviewer-diff')).toContainText('Line 90');
  });
});
