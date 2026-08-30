import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';

/**
 * Real product journey over the HTTP, DOM, and WebSocket boundaries. The
 * throwaway server always exposes either the licensed local fixture or an
 * explicitly fictional non-operational CI fixture, so this never depends on a
 * live model/provider and never skips the workflow state machine.
 */

test.describe.configure({ timeout: 120_000 });

type QueueRow = {
  protocol_id: string;
  execution_readiness: { state: string; can_approve_for_execution: boolean };
};

async function reviewQueue(request): Promise<QueueRow[]> {
  const response = await request.get('/api/protocols/review-queue');
  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  return Array.isArray(body.protocols) ? body.protocols : [];
}

function checkpointSession(protocolId: string) {
  const localPython = path.join(process.cwd(), '.venv', 'bin', 'python');
  const activePython = process.env.VIRTUAL_ENV
    ? path.join(process.env.VIRTUAL_ENV, 'bin', 'python')
    : '';
  const python = activePython && existsSync(activePython)
    ? activePython
    : existsSync(localPython) ? localPython : 'python3';
  const output = execFileSync(python, [
    '-B', 'scripts/bootstrap_browser_checkpoint_session.py',
    '--protocol-data-dir', path.join(process.cwd(), 'data/runtime/ci-e2e'),
    '--workspace-data-dir', path.join(process.cwd(), 'data/runtime/ci-e2e/workspace'),
    '--protocol-id', protocolId,
    '--current-step-label', '6',
  ], { cwd: process.cwd(), encoding: 'utf8' });
  return JSON.parse(output.trim());
}

test.describe('Reviewer → researcher protocol journey', () => {
  test('real clicks approve execution and drive the exact checkpoint loop', async ({ page, request }, testInfo) => {
    test.skip(testInfo.project.name !== 'Desktop Chrome', 'the stateful product journey runs once; responsive coverage is separate');

    const queue = await reviewQueue(request);
    expect(queue.length).toBeGreaterThan(0);
    const protocolId = queue[0].protocol_id;
    const initial = await (await request.get(`/api/protocols/${protocolId}/review`)).json();
    expect(initial.analysis_available).toBe(true);
    expect(initial.execution_readiness.can_approve_for_execution).toBe(false);
    for (const checkpoint of initial.human_checkpoints ?? []) {
      expect(checkpoint.blocks_execution).toBe(false);
      expect(checkpoint.display_label).toBe('연구자 확인 단계');
    }

    // Reviewer queue → real click → source interpretation → readiness.
    await page.goto('/');
    await page.locator('#workspace-reviewer').waitFor({ state: 'visible', timeout: 20_000 });
    await page.locator('#workspace-reviewer').click();
    const queueAction = page.locator('#reviewer-protocol-queue button', { hasText: '검토하기' }).first();
    await expect(queueAction).toBeVisible({ timeout: 20_000 });
    await queueAction.click();
    await expect(page.locator('#reviewer-selection')).toContainText(initial.title);
    await expect(page.locator('#reviewer-attention')).toContainText('원문 해석 확인 필요');
    await expect(page.locator('#reviewer-approve')).toBeDisabled();

    const issue = initial.needs_resolution[0];
    await page.locator('#reviewer-attention button', { hasText: '해석 확인하기' }).first().click();
    const values: string[] = (issue.selectable_steps ?? []).map((step) => step.step_id);
    const gateIndex = values.indexOf(issue.step_id);
    const range = values.slice(Math.max(0, gateIndex - 3), gateIndex + 1);
    expect(range.length).toBeGreaterThan(0);
    await page.locator('#reviewer-resolution-range').selectOption(range);
    await page.locator('#reviewer-resolution-interpretation').fill(
      '원문이 가리키는 기존 단계 범위를 확인했습니다.',
    );
    await page.locator('#reviewer-resolution-rationale').fill(
      '원문 페이지와 기존 단계 순서를 대조했습니다.',
    );
    await page.locator('#reviewer-resolution-save').click();
    await expect(page.locator('#reviewer-readiness')).toContainText('실행 승인 가능');
    await expect(page.locator('#reviewer-approve')).toBeEnabled();

    // Real approval click and consequence confirmation.
    await page.locator('#reviewer-comment').fill('원문, 반복 범위, 실행 준비 상태를 확인했습니다.');
    await page.locator('#reviewer-approve').click();
    await expect(page.locator('#reviewer-decision-confirmation')).toBeVisible();
    await page.locator('#reviewer-decision-confirm').click();
    await expect(page.locator('#reviewer-status')).toContainText('연구자 사용 승인 결정', { timeout: 20_000 });
    const approved = await (await request.get(`/api/protocols/${protocolId}`)).json();
    expect(approved.available_for_execution).toBe(true);

    // Build a legitimate durable state through WorkspaceStore: steps 1-5 are
    // append-only completed records and step 6 remains incomplete for the UI.
    const fixture = checkpointSession(protocolId);
    expect(fixture.current_step_label).toBe('6');

    // Researcher selection → recovery → normal step completion.
    await page.goto('/');
    await expect(page.locator(`#protocol-id option[value="${protocolId}"]`)).toHaveCount(1, { timeout: 20_000 });
    await page.locator('#experiment-session-ledger > summary').click();
    await page.locator('#experiment-session-select').selectOption(fixture.session_id);
    await expect(page.locator('#start')).toHaveText('실험 이어하기');
    await page.locator('#start').click();
    await expect(page.locator('#procedure-progress')).toContainText('현재 6/', { timeout: 30_000 });
    await expect(page.locator('#switch-to-manual')).toBeVisible();
    await page.locator('#switch-to-manual').click();
    await expect(page.locator('#state')).toHaveText('수동 실행');
    await expect(page.locator('#complete-current-step')).toBeEnabled();
    await page.locator('#complete-current-step').click();
    await expect(page.locator('#human-checkpoint')).toBeVisible();
    await expect(page.locator('#procedure-progress')).toContainText('현재 7/');
    await expect(page.locator('#bench-action-status')).toContainText('완료로 저장');

    // not_met records the human decision and returns to the exact source range.
    const reviewedProtocol = await (await request.get(`/api/protocols/${protocolId}/review`)).json();
    const checkpoint = reviewedProtocol.human_checkpoints.find(
      (item) => item.gate_step_label === '7',
    );
    expect(checkpoint.repeated_step_labels[0]).toBe('2');
    await page.locator('#human-checkpoint-not-met').click();
    await expect(page.locator('#human-checkpoint')).toBeHidden();
    await expect(page.locator('#procedure-progress')).toContainText('현재 2/');

    // Re-run the exact represented range through the same server completion
    // boundary, return to the checkpoint, then record met and advance once.
    for (const label of ['2', '3', '4', '5', '6']) {
      await expect(page.locator('#procedure-progress')).toContainText(`현재 ${label}/`);
      await expect(page.locator('#complete-current-step')).toBeEnabled();
      await page.locator('#complete-current-step').click();
    }
    await expect(page.locator('#procedure-progress')).toContainText('현재 7/');
    await expect(page.locator('#human-checkpoint')).toBeVisible();
    await page.locator('#human-checkpoint-met').click();
    await expect(page.locator('#procedure-progress')).toContainText('현재 8/');

    // The credential-free CI fixture also exercises a reviewer-resolved source
    // ambiguity at step 8. Candidate A has no second gate, so this branch is
    // driven solely by the server's reviewed protocol projection.
    const resolvedCheckpoint = reviewedProtocol.human_checkpoints.find(
      (item) => item.gate_step_label === '8',
    );
    if (resolvedCheckpoint) {
      await expect(page.locator('#human-checkpoint')).toBeVisible();
      await expect(page.locator('#human-checkpoint-source-text')).toContainText(
        resolvedCheckpoint.condition_source_text,
      );
      await page.locator('#human-checkpoint-met').click();
    }
    await expect(page.locator('#human-checkpoint')).toBeHidden();

    const timeline = await (await request.get(
      `/api/workspace/experiments/${fixture.session_id}/timeline`,
    )).json();
    const repeatEvent = timeline.timeline.find(
      (event) => event.event_type === 'human_checkpoint_repeat_scheduled',
    );
    const metEvent = timeline.timeline.find(
      (event) => event.event_type === 'human_checkpoint_confirmed'
        && event.payload.checkpoint_id === checkpoint.checkpoint_id,
    );
    expect(repeatEvent.payload.repeated_step_ids).toEqual(checkpoint.repeated_step_ids);
    expect(metEvent.payload.checkpoint_id).toBe(checkpoint.checkpoint_id);
    expect(timeline.session.current_step_label).toBe('8');

    // A denied microphone must not tear down the accepted experiment. The
    // researcher can start and advance the exact approved protocol through
    // revision-fenced bench actions while voice remains unavailable.
    await page.locator('#rail-new-session').click();
    await expect(page.locator('#new-session-modal')).toBeVisible();
    await page.locator('#modal-confirm-btn').click();
    await page.evaluate(() => {
      Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
        configurable: true,
        value: async () => {
          throw new DOMException('microphone permission denied', 'NotAllowedError');
        },
      });
    });
    await expect(page.locator('#start')).toBeEnabled();
    await page.locator('#start').click();
    await expect(page.locator('#state')).toHaveText('수동 실행', { timeout: 30_000 });
    await expect(page.locator('#microphone-status')).toContainText('마이크를 사용할 수 없습니다');
    await expect(page.locator('#procedure-progress')).toContainText('현재 1/');
    await expect(page.locator('#manual-fallback-action')).toBeVisible();
    await expect(page.locator('#manual-start-protocol')).toBeEnabled();
    await page.locator('#manual-start-protocol').click();
    await expect(page.locator('#manual-fallback-status')).toContainText('프로토콜 시작을 저장');
    await expect(page.locator('#complete-current-step')).toBeEnabled();
    await page.locator('#complete-current-step').click();
    await expect(page.locator('#procedure-progress')).toContainText('현재 2/');
    await expect(page.locator('#state')).toHaveText('수동 실행');
  });
});
