import type { Page } from '@playwright/test';

// Lane RX1 (2026-10-25): the new screen's upload, analysis progress,
// confirmation and today's-values walks. Every server answer the walks need
// beyond the CI server (dev mode, empty catalog) is mocked with page.route;
// the page images are one small PNG made here.

export const APP = '/app/';

// A 2 x 2 white PNG (valid, a few dozen bytes); the overlay math reads the image's natural size only.
export const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAC0lEQVR4nGP4DwQACfsD/Wu6DdUAAAAASUVORK5CYII=',
  'base64',
);

export const UPLOADED = {
  protocol_id: 'p-rx1-new', title: '표지 최종 하리', source_filename: 'western-blot.pdf', source_sha256: 'b'.repeat(64), revision_id: 'pdf-1',
  readiness_status: 'analysis_required', analysis_status: 'structured_analysis_ready', step_count: 0, created_at: '2026-10-25T01:00:00+00:00',
  available_for_execution: false, development_only: false, lifecycle_state: 'uploaded', execution_blocker_codes: [], analysis_outdated_codes: [],
  analysis_outdated_ko: null, review_pending: null, review_versions: [], current_review_version: null,
};

export const SOURCE_PAGE = (page: number) => ({
  protocol_id: 'p-rx1-new', page, page_count: 3, ocr: false, image_dpi: 120,
  lines: [
    { number: 1, text: 'Protocol Gamma', has_value: false, unclear: false, box: [72, 52, 150, 64] },
    { number: 2, text: 'Section preparation', has_value: false, unclear: false, box: [72, 66, 170, 78] },
    { number: 3, text: '1. Add 2 mL of buffer A.', has_value: true, unclear: false, box: [72, 80, 200, 92] },
    { number: 4, text: '2. Incubate for 10 min at 37 C.', has_value: true, unclear: false, box: [72, 94, 230, 106] },
    { number: 5, text: 'Wear gloves.', has_value: false, unclear: false, box: [72, 108, 140, 120] },
  ],
});

const choice = (choice_id: string, label_ko: string, request: object | null, extra: object = {}) => ({ choice_id, label_ko, request, needs: null, route: null, primary: false, ...extra });

export const CARD_EVIDENCE = {
  card_id: 'step:s2', item_id: 'step:s2', position: 1, total: 2, progress_ko: '1 / 2', type: 'evidence', kind: 'step', kind_ko: '단계', step_label: '2',
  question_ko: '이 단계의 근거를 원문에서 찾지 못했어요. 원문의 어느 줄인가요?', reasons_ko: ['원문에 없음'], shown: '2. Incubate for 10 min at 37 C.',
  source: { page: 1, excerpt: '2. Incubate for 10 min at 37 C.', verified: false, lines: null, suggested_lines: { page: 1, first_line: 4, last_line: 4, unique: false } },
  choices: [
    choice('confirm_lines', '고른 줄이 맞아요', { item_id: 'step:s2', action: 'confirm' }, { needs: 'source_lines', primary: true }),
    choice('fix', '직접 고칠게요', { item_id: 'step:s2', action: 'fix' }, { needs: 'source_lines_and_text' }),
    choice('reanalysis', '다시 분석', null, { route: 'reanalysis' }),
  ],
  proposal: null, change: null, readings: null, steps: null, memo_allowed: true, decided: false, decision: null, current: '2. Incubate for 10 min at 37 C.',
};

export const CARD_CLAIM = {
  card_id: 'value:s4', item_id: 'value:s4', position: 2, total: 2, progress_ko: '2 / 2', type: 'claim', kind: 'value', kind_ko: '값', step_label: '4',
  question_ko: '이 값이 원문과 같나요?', reasons_ko: [], shown: '500 x g',
  source: { page: 2, excerpt: '4. Spin at 500 x g for 5 min.', verified: true, lines: { page: 2, first_line: 3, last_line: 3 }, suggested_lines: null },
  choices: [
    choice('confirm', '맞아요', { item_id: 'value:s4', action: 'confirm' }, { primary: true }),
    choice('fix', '직접 고칠게요', { item_id: 'value:s4', action: 'fix' }, { needs: 'text' }),
    choice('clear', '원문에 없어요', { item_id: 'value:s4', action: 'clear' }),
  ],
  proposal: null, change: null, readings: null, steps: null, memo_allowed: true, decided: false, decision: null, current: '500 x g',
};

export const TARGET = { kind: 'review_pending', revision_id: 'pdf-1', analysis_id: 'analysis-rx1' };

export function draftWith(cards: object[], nextCard: number | null) {
  return {
    protocol_id: 'p-rx1-new', title: '표지 최종 하리', source_filename: 'western-blot.pdf', analysis_status: 'review_pending', available_for_execution: false,
    review_pending: { needs_review_count: 2 }, target: TARGET, items: [], cards, next_card: nextCard, needs_review: ['step:s2', 'value:s4'],
    counts: { items: 9, needs_review: 2, decided: 0, undecided: 2, changed: 0 }, pages: [], registrable: nextCard === null, registration_blockers: [],
  };
}

export const RUN_OPTIONS = {
  protocol_id: 'p-rx1-ready', target: { kind: 'analysis', revision_id: 'pdf-1-analysis-1', analysis_id: 'analysis-1' }, revision_id: 'pdf-1-analysis-1', limit: 12,
  values: [
    { item_id: 's2#1', step_id: 's2', step_label: '2', kind: 'time', kind_ko: '시간', number: '10', unit: 'min', written: '10 min', shown_ko: '10분', units_ko: '초·분·시간', start: 16, end: 22 },
    { item_id: 's2#2', step_id: 's2', step_label: '2', kind: 'temperature', kind_ko: '온도', number: '37', unit: '°C', written: '37 C', shown_ko: '37°C', units_ko: '°C', start: 26, end: 30 },
    { item_id: 's4#1', step_id: 's4', step_label: '4', kind: 'speed', kind_ko: '속도', number: '500', unit: '×g', written: '500 x g', shown_ko: '500 ×g', units_ko: '×g', start: 11, end: 18 },
  ],
  steps: [
    { step_id: 's1', step_label: '1', text: '1. Add 2 mL of buffer A.', text_ko: '1. 완충액 A 2 mL를 넣어요.', value_ids: [] },
    { step_id: 's2', step_label: '2', text: '2. Incubate for 10 min at 37 C.', text_ko: '2. 37 °C에서 10분 동안 둡니다.', value_ids: ['s2#1', 's2#2'] },
    { step_id: 's4', step_label: '4', text: '4. Spin at 500 x g for 5 min.', text_ko: null, value_ids: ['s4#1'] },
  ],
  last_time: null,
};

/** The whole walk's server: upload → page pictures → analysis run → cards → registration; today's values for a runnable protocol. */
export async function mockProtocolScreens(page: Page) {
  const protocols: Record<string, object> = {};
  let runState: 'idle' | 'running' | 'review' | 'ready' = 'idle';
  let polls = 0;
  let cards = [CARD_EVIDENCE, CARD_CLAIM];
  const decisions: object[] = [];
  const analysisRequests: string[] = [];
  let registered = false;
  let runChanges: object | null = null;
  // The protocol's title as the ledger holds it: the read one until a person renames it.
  let title = UPLOADED.title;

  await page.route('**/api/auth/session', (route) => route.fulfill({ json: { mode: 'dev', signed_in: true, account: null } }));

  await page.route('**/api/protocols', (route) =>
    route.fulfill({ json: { protocols: Object.values(protocols) } }),
  );
  await page.route(/\/api\/protocols\?filename=/, async (route) => {
    protocols[UPLOADED.protocol_id] = UPLOADED;
    await route.fulfill({ status: 201, json: { protocol: UPLOADED, deduplicated: false, ocr: { state: 'not_required', automatic: false } } });
  });
  await page.route('**/api/protocols/p-rx1-new/review-draft/source-pages', (route) => route.fulfill({ json: { protocol_id: 'p-rx1-new', page_count: 3, thumbnail_dpi: 36, image_dpi: 120 } }));
  await page.route(/\/api\/protocols\/p-rx1-new\/review-draft\/source-pages\/\d+\/image/, (route) => route.fulfill({ contentType: 'image/png', body: PNG }));
  await page.route(/\/api\/protocols\/p-rx1-new\/review-draft\/source-pages\/(\d+)$/, (route) => {
    const n = Number(route.request().url().split('/').pop());
    return route.fulfill({ json: SOURCE_PAGE(n) });
  });
  await page.route(/\/api\/protocols\/p-rx1-new\/analysis(\?.*)?$/, (route) => {
    analysisRequests.push(route.request().url());
    runState = 'running';
    polls = 0;
    return route.fulfill({ status: 202, json: { ...UPLOADED, analysis_status: 'analyzing', analysis_request_accepted: true, analysis_run: { state: 'analyzing' } } });
  });
  await page.route('**/api/protocols/p-rx1-new', (route) => {
    // The run ends with cards after two polls (the screen polls every 5 s; the first read is at once).
    if (runState === 'running') {
      polls += 1;
      if (polls >= 2) runState = 'review';
    }
    const run =
      runState === 'running'
        ? { state: 'chunk_analysis_in_progress', requested_at: new Date(Date.now() - 90_000).toISOString(), total_chunks: 2, completed_chunks: 1, chunks: [{ chunk_id: 'piece-1', source_page_start: 1, source_page_end: 2, status: 'completed' }, { chunk_id: 'piece-2', source_page_start: 3, source_page_end: 3, status: 'in_progress' }], reanalysis: false, failure_code: null }
        : runState === 'review'
          ? { state: 'review_pending', requested_at: new Date(Date.now() - 120_000).toISOString(), total_chunks: 2, completed_chunks: 2, reanalysis: false, failure_code: null }
          : runState === 'ready'
            ? { state: 'complete', reanalysis: false, failure_code: null }
            : { state: 'uploaded', reanalysis: false, failure_code: null };
    const entry = {
      ...UPLOADED,
      title,
      analysis_status: runState === 'running' ? 'chunk_analysis_in_progress' : runState === 'review' ? 'review_pending' : runState === 'ready' ? 'structured_analysis_ready' : 'structured_analysis_ready',
      lifecycle_state: runState === 'running' ? 'analyzing' : runState === 'review' ? 'review_pending' : runState === 'ready' ? 'ready' : 'uploaded',
      available_for_execution: runState === 'ready',
      review_pending: runState === 'review' ? { needs_review_count: 2, message_ko: '검토 대기 · 확인 필요 2건' } : null,
      analysis_run: run,
    };
    protocols[UPLOADED.protocol_id] = entry;
    return route.fulfill({ json: entry });
  });
  await page.route('**/api/protocols/p-rx1-new/review-draft', (route) => route.fulfill({ json: draftWith(cards, cards.findIndex((card) => !(card as { decided: boolean }).decided) >= 0 ? cards.findIndex((card) => !(card as { decided: boolean }).decided) : null) }));
  await page.route('**/api/protocols/p-rx1-new/review-decisions', (route) => {
    const body = route.request().postDataJSON();
    decisions.push(body);
    cards = cards.map((card) => ((card as { item_id: string }).item_id === body.item_id ? { ...card, decided: true, decision: { action: body.action, source_lines: body.source_lines ?? null, value: body.text ? { text: body.text } : null } } : card));
    const next = cards.findIndex((card) => !(card as { decided: boolean }).decided);
    return route.fulfill({ json: draftWith(cards, next >= 0 ? next : null) });
  });
  await page.route('**/api/protocols/p-rx1-new/review-versions', (route) => {
    registered = true;
    runState = 'ready';
    return route.fulfill({ status: 201, json: { version: 1 } });
  });
  await page.route('**/api/protocols/p-rx1-new/title', (route) => {
    const body = route.request().postDataJSON();
    title = body.title;
    protocols[UPLOADED.protocol_id] = { ...(protocols[UPLOADED.protocol_id] ?? UPLOADED), title };
    return route.fulfill({ json: { title, protocol: protocols[UPLOADED.protocol_id] } });
  });
  await page.route('**/api/protocols/p-rx1-ready', (route) => route.fulfill({ json: { ...UPLOADED, protocol_id: 'p-rx1-ready', title: 'Protocol Gamma', source_filename: 'gamma.pdf', available_for_execution: true, lifecycle_state: 'ready', analysis_run: { state: 'complete' } } }));
  await page.route('**/api/protocols/p-rx1-ready/start-check/changes', (route) => {
    if (route.request().method() !== 'POST') return route.fulfill({ json: RUN_OPTIONS });
    const body = route.request().postDataJSON();
    runChanges = body;
    const changes = (body.changes as Array<{ item_id: string; value: string }>).map((item) => {
      const value = RUN_OPTIONS.values.find((row) => row.item_id === item.item_id)!;
      return { item_id: item.item_id, step_label: value.step_label, kind: value.kind, kind_ko: value.kind_ko, source_number: value.number, source_unit: value.unit, number: item.value.replace(/[^\d.]/g, ''), unit: value.unit, shown_ko: item.value, source_shown_ko: value.shown_ko, words_ko: `${item.value}(오늘 바꾼 값 · 원문 ${value.shown_ko})` };
    });
    return route.fulfill({ json: { change_set_id: 'run-changes-rx1', changes, source: body.source } });
  });
  await page.route('**/api/workspace/experiments', (route) => route.fulfill({ json: { experiments: [] } }));
  await page.route('**/api/workspace/session', (route) => route.fulfill({ json: { principal_id: 'dev', display_name: '개발 로그인', organization_id: 'dev' } }));

  return {
    decisions,
    analysisRequests,
    isRegistered: () => registered,
    runChanges: () => runChanges,
  };
}
