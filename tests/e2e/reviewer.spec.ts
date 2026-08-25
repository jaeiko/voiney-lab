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
  test('shows the inbox, impact-first summary, audit, and decision panels', async ({ page }) => {
    await openReviewerWorkspace(page);
    await expect(page.locator('#reviewer-inbox')).toBeVisible();
    await expect(page.locator('#reviewer-change-summary')).toBeVisible();
    await expect(page.locator('#reviewer-impact')).toContainText('평가되지 않음');
    await expect(page.locator('#reviewer-risk')).toContainText('평가되지 않음');
    await expect(page.locator('#reviewer-history')).toBeVisible();
    await expect(page.locator('#reviewer-diff')).toBeHidden();
    await page.locator('#reviewer-technical-details').click();
    await expect(page.locator('#reviewer-diff')).toBeVisible();
    await expect(page.locator('#reviewer-approve')).toBeVisible();
    await expect(page.locator('#reviewer-approve')).toHaveText('승인');
    await expect(page.locator('#reviewer-reject')).toBeVisible();
    await expect(page.locator('#reviewer-reject')).toHaveText('수정 요청');
    await expect(page.locator('#reviewer-revoke')).toBeVisible();
    await expect(page.locator('#reviewer-revoke')).toHaveText('향후 사용 중지');
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
    await expect(page.locator('#reviewer-confirm-consequence')).toContainText('새 운영 실험');
    await page.locator('#reviewer-decision-cancel').click();
    await expect(page.locator('#reviewer-decision-confirmation')).toBeHidden();
    await expect(page.locator('#reviewer-status')).toContainText('프로토콜 상태는 변경되지 않았습니다');
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
    await expect(page.locator('#protocols-io-import')).toBeVisible();
    await expect(page.locator('#drive-sync')).toBeVisible();
    await expect(page.locator('#github-import')).toBeVisible();
    await expect(page.locator('#github-status')).toContainText('실행하지 않습니다');
  });

  test('a short inbox stays compact beside a long readable decision record', async ({ page }) => {
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
    if (layout.viewportWidth > 980) {
      expect(layout.inboxCardHeight + 100).toBeLessThan(layout.decisionCardHeight);
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
