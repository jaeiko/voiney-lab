import { test, expect, type Page } from '@playwright/test';

// Lane U2 (2026-10-06), decisions 5–7: the remaining screen words. Server
// answers are mocked with page.route or fed to the page's own message
// handler; nothing talks to a model or a speech provider. The screen changes
// how the server's values are shown, never what the server decided.

const PROTOCOL_ID = 'protocol-ingel-0001';
const REVISION_ID = 'fixture-69517f0fe629d0e4dc35';

const entry = (runnable: boolean) => ({
  protocol_id: PROTOCOL_ID, title: 'In-gel digestion', source_filename: 'in-gel-digestion.pdf',
  source_sha256: 'a'.repeat(64), revision_id: REVISION_ID,
  readiness_status: runnable ? 'guidance_ready' : 'analysis_required',
  approval_status: 'development_only', analysis_status: 'analysis_complete', step_count: 25,
  created_at: '2026-10-05T01:00:00+00:00', available_for_execution: runnable, development_only: true,
  lifecycle_state: runnable ? 'ready' : 'blocked',
  approval: { status: 'development_only', final_approval: false, actor_principal_id: 'local-admin', actor_role: 'lab_admin', recorded_at: '2026-10-05T01:02:00+00:00' },
});

const review = {
  protocol_id: PROTOCOL_ID, revision_id: 'pdf-1-analysis-2', source: { filename: 'in-gel-digestion.pdf', page_count: 9 },
  readiness: { status: 'analysis_required', reasons: [] }, gates: {},
  constructs: [{ construct_type: 'SourceAmbiguity', ambiguity_id: 'amb-1', step_id: 'step-4', resolved: false }],
  outstanding_blockers: [{
    code: 'unresolved_ambiguity', kind: 'reviewer_can_clear', step_id: 'step-4', source_page_number: 4,
    reviewer_action: 'resolve_ambiguity', already_acknowledged: false,
    decision_options: ['single_statement_is_authoritative', 'distinct_statements'], clearing_decision: 'single_statement_is_authoritative',
    citable_segments: [{ segment_id: 'seg-p4-0', source_page_number: 4, segment_index: 0, excerpt: 'Incubate the gel pieces at 37 °C for 30 min.' }],
  }],
};

const fixtureState = (overrides: Record<string, unknown> = {}) => ({
  attached: true, protocol_id: PROTOCOL_ID, revision_id: REVISION_ID, display_name: 'In-gel digestion', development_only: true,
  readiness_status: 'analysis_required', active: true, current_step_label: '1', current_step_id: 'step-1', total_steps: 25,
  at_final_step: false, block_reason: null, revision: 5, display_summary: '1 Cut the gel band.',
  primary_summary: '1단계: 젤 밴드를 자릅니다.', source_language: 'en', spoken_summary: '1단계입니다.',
  warning_texts: [], warning_presentations: [], visual_assets: [], visual_status: 'unavailable', source_page_refs: [2],
  workflow_status: 'running', ...overrides,
});

async function startStep(page: Page, state: object) {
  await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [entry(true)] } }));
  await page.goto('/');
  await page.waitForFunction(() => typeof renderCuratedProtocolState === 'function');
  await page.evaluate(([pid, rid, value]) => {
    acceptedSessionConfiguration = { configuration_id: 7, server_generation: 0, mode: 'cascade', language: 'ko', protocol_id: pid, revision_id: rid };
    sessionActive = true;
    onMessage({ data: JSON.stringify({ type: 'protocol.fixture.state', configuration_id: 7, action: 'start', state: value }) }, sessionGeneration, socket);
  }, [PROTOCOL_ID, REVISION_ID, state] as const);
}

const send = (page: Page, message: object) => page.evaluate(m => onMessage({ data: JSON.stringify(m) }, sessionGeneration, socket), message);

async function capabilities(page: Page, web: 'enabled' | 'disabled') {
  await send(page, { type: 'ready', research_capabilities: {
    external_text: { status: web }, supplemental_model: { status: 'enabled' }, web_image: { status: 'disabled' }, generated_visual: { status: 'disabled' } } });
}

const visibleText = (page: Page, selector: string) => page.locator(selector).evaluate(node => (node as HTMLElement).innerText);

test.describe('Remaining screen words (lane U2)', () => {
  test('the step is named once in Korean and the timer reads minutes left', async ({ page }) => {
    await startStep(page, fixtureState({ timers: { step: { state: 'running', duration_seconds: 900, deadline_at: new Date(Date.now() + 895_500).toISOString() } } }));
    await expect(page.locator('#procedure-progress')).toHaveText('1단계 · 전체 25단계');
    await expect(page.locator('#procedure-step-title')).toBeHidden();
    await expect(page.locator('#rail-step-badge')).toHaveText('1단계 / 25');
    await expect(page.locator('#rail-timer-badge')).toHaveText(/^⏱ 14:5\d 남음$/);
    await expect(page.locator('#procedure-timer')).toHaveText(/단계 14:5\d 남음/);
    const card = await visibleText(page, '.procedure-card');
    expect(card).not.toMatch(/\bStep \d/);
    expect(card).not.toMatch(/\d+s\b/);
  });

  test('nothing handed over is not a warning, and the readiness status is a developer detail', async ({ page }) => {
    await startStep(page, fixtureState());
    await expect(page.locator('#handoff-container')).toBeHidden();
    await expect(page.locator('#audit-container')).toBeHidden();
    const card = await visibleText(page, '.procedure-card');
    for (const phrase of ['넘긴 보고 없음', '확인 기록', '준비 상태', '프로토콜 분석 필요']) expect(card).not.toContain(phrase);
    const dev = await page.locator('#procedure-meta .dev-code-details').evaluate(node => node.textContent || '');
    expect(dev).toContain('readiness_status · analysis_required');
    expect(dev).toContain('development_only · true');

    // A real block is still shown, in red, with its reason.
    await send(page, { type: 'protocol.fixture.state', configuration_id: 7, action: 'block', state: fixtureState({ revision: 6, block_reason: 'procedure_blocked_for_handoff' }) });
    await expect(page.locator('#handoff-container')).toBeVisible();
    await expect(page.locator('#procedure-handoff')).toContainText('진행 제한');
    const colour = await page.locator('#procedure-handoff').evaluate(node => getComputedStyle(node).color);
    expect(colour).toBe('rgb(250, 119, 124)');
  });

  test('review choices say what they do in plain words, and an empty check list is not drawn', async ({ page }) => {
    await page.route('**/api/protocols', route => route.fulfill({ json: { protocols: [entry(false)] } }));
    await page.route(`**/api/protocols/${PROTOCOL_ID}/review`, route => route.fulfill({ json: review }));
    await page.goto('/');
    await page.waitForFunction(() => typeof renderProtocolReview === 'function');
    await page.locator('#protocol-id').selectOption(PROTOCOL_ID);
    await page.locator('#protocol-blockers .citation-row').first().waitFor();
    const options = await page.locator('#protocol-blockers select option').allTextContents();
    expect(options).toContain('한 진술이 기준임 · 이 근거로 해결된 것으로 표시');
    expect(options.some(text => text.endsWith('기록만 하고 해결로 표시하지 않음'))).toBe(true);
    expect(options.join(' ')).not.toContain('사유를 해제');
    const headings = await page.locator('#protocol-review-content h4').allTextContents();
    expect(headings).not.toContain('실행 전 확인 조건');
  });

  test('an explanation from outside the PDF has its own frame and name in the card and the panel', async ({ page }) => {
    await startStep(page, fixtureState());
    await capabilities(page, 'disabled');
    // A router answer the server marked as outside the PDF: the mark comes
    // in reply.delta and reply.complete carries the text alone.
    await send(page, { type: 'speech.start', turn_id: 1, generation: 0 });
    await send(page, { type: 'transcript', turn_id: 1, generation: 0, text: '중탄산암모늄은 왜 넣어?' });
    await send(page, { type: 'reply.delta', turn_id: 1, generation: 0, text: '일반적으로 pH 를 맞추는 완충액입니다.', answer_origin: 'supplemental_model_knowledge',
      limitations: ['outside_pdf_explanation'],
      display_document: { title: '1단계', sections: [{ kind: 'notice', text: 'PDF 밖 설명이니 유의' }, { kind: 'section', heading: '', text: '일반적으로 pH 를 맞추는 완충액입니다.' }] } });
    await send(page, { type: 'reply.complete', turn_id: 1, generation: 0, text: '일반적으로 pH 를 맞추는 완충액입니다.' });
    const frame = page.locator('#log .reply .outside-pdf').first();
    await expect(frame.locator('.outside-pdf-label')).toHaveText('PDF 밖 설명 · AI 일반 지식');
    await expect(frame).toContainText('PDF 밖 설명이니 유의');
    await expect(frame).toContainText('pH 를 맞추는 완충액');
    expect(await frame.evaluate(node => getComputedStyle(node).borderTopStyle)).toBe('dashed');

    // The explanation said after the rules' answer (research.result, lane R6).
    await send(page, { type: 'speech.start', turn_id: 2, generation: 0 });
    await send(page, { type: 'reply.complete', turn_id: 2, generation: 0, text: 'PDF에는 따로 설명이 없어요.' });
    await send(page, { type: 'research.state', configuration_id: 7, turn_id: 2, generation: 0, status: 'running', phase: 'supplemental_model' });
    await send(page, { type: 'research.result', configuration_id: 7, turn_id: 2, generation: 0, status: 'success', terminal_status: 'success',
      primary_text: 'PDF에는 따로 설명이 없어요. 일반적으로는 유기 용매입니다.', answer_origin: 'supplemental_model_knowledge',
      citations: [], limitations: ['outside_pdf_explanation'], source_label: 'AI 일반 지식', outside_pdf: true });
    const block = page.locator('#log .research-supplement.outside-pdf');
    await expect(block.locator('b')).toHaveText('PDF 밖 설명 · AI 일반 지식');
    await expect(page.locator('#web-ref-title')).toHaveText('PDF 밖 설명 · AI 일반 지식');
    await expect(page.locator('#task-web-reference-content .outside-pdf')).toContainText('유기 용매');
  });

  test('the reference panel is named by where its words come from', async ({ page }) => {
    await startStep(page, fixtureState());
    await capabilities(page, 'enabled');
    const result = (turn: number, extra: object) => send(page, { type: 'research.result', configuration_id: 7, turn_id: turn, generation: 0, status: 'success', terminal_status: 'success', ...extra });
    for (const turn of [1, 2]) {
      await send(page, { type: 'speech.start', turn_id: turn, generation: 0 });
      await send(page, { type: 'transcript', turn_id: turn, generation: 0, text: `질문 ${turn}` });
      await send(page, { type: 'research.state', configuration_id: 7, turn_id: turn, generation: 0, status: 'running', phase: turn === 1 ? 'supplemental_model' : 'authoritative_web' });
    }
    await result(1, { primary_text: '일반적인 배경 설명', answer_origin: 'supplemental_model_knowledge', citations: [] });
    await expect(page.locator('#web-ref-title')).toHaveText('AI 일반 지식');
    await expect(page.locator('#task-web-reference-content .outside-pdf')).toHaveCount(0);
    await result(2, { primary_text: '권위 자료의 설명', answer_origin: 'external_authoritative_reference',
      citations: [{ title: 'Lab safety', domain: 'osha.gov', canonical_url: 'https://osha.gov/laboratory' }] });
    await expect(page.locator('#web-ref-title')).toHaveText('웹 참고 자료');
  });

  test('with web search off, a web-reference limit card stays in the developer details', async ({ page }) => {
    await startStep(page, fixtureState());
    await capabilities(page, 'disabled');
    await send(page, { type: 'speech.start', turn_id: 3, generation: 0 });
    await send(page, { type: 'reply.complete', turn_id: 3, generation: 0, text: 'PDF에서 확인할 수 없어요.' });
    await send(page, { type: 'research.state', configuration_id: 7, turn_id: 3, generation: 0, status: 'running', phase: 'authoritative_web' });
    await send(page, { type: 'research.result', configuration_id: 7, turn_id: 3, generation: 0, status: 'unavailable', terminal_status: 'disabled',
      limitation: '추가 내용을 확인하지 못했습니다. 현재 적용된 실험 PDF 내용은 유지됩니다.' });
    const reply = page.locator('#log .reply').first();
    await expect(reply).not.toContainText('웹 참고 자료');
    await expect(reply.locator('.research-supplement')).toHaveCount(0);
    expect(await page.locator('#log .turn-diagnostics-body .research-limit').first().evaluate(node => node.textContent)).toContain('disabled');

    // With web search on, the same card is shown to the researcher.
    await capabilities(page, 'enabled');
    await send(page, { type: 'speech.start', turn_id: 4, generation: 0 });
    await send(page, { type: 'transcript', turn_id: 4, generation: 0, text: '트립신 보관 온도는?' });
    await send(page, { type: 'research.state', configuration_id: 7, turn_id: 4, generation: 0, status: 'running', phase: 'authoritative_web' });
    await send(page, { type: 'research.result', configuration_id: 7, turn_id: 4, generation: 0, status: 'unavailable', terminal_status: 'timeout', limitation: '시간 안에 찾지 못했습니다.' });
    await expect(page.locator('#log .research-supplement b', { hasText: '웹 참고 자료 확인 시간 초과' })).toHaveCount(1);
  });
});
