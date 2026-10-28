import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { RecordsScreen, RecordDetailScreen, NotebookScreen } from './RecordsScreen';
import { fakeFetch, renderWithClient, SESSION_DEV } from '../test/helpers';
import { RECORDS, REPORT, TIMELINE, NOTEBOOK } from '../test/records';
import type { AuthSession } from '../api/types';

afterEach(() => vi.unstubAllGlobals());
function setup(overrides: Parameters<typeof fakeFetch>[0] = {}) { return fakeFetch({
  '/api/workspace/experiments': { json: { experiments: RECORDS } },
  '/api/workspace/session': { json: { principal_id: 'me' } },
  '/api/workspace/experiments/rx3-0': { json: RECORDS[0] },
  '/api/workspace/experiments/rx3-0/timeline': { json: TIMELINE },
  '/api/workspace/experiments/rx3-0/reports': { json: { reports: [REPORT] } },
  '/api/experiment-reports/report-rx3/prose': { json: { state: 'ready', label: '보고서 문장이 준비됐어요.' } },
  '/api/experiment-reports/report-rx3/notebook': { json: NOTEBOOK },
  '/api/experiment-reports/report-rx3/sharing': { json: { report_id: 'report-rx3', shared: false, changed_at: null } },
  '/api/experiment-reports/report-rx3/comparisons': { json: { records: [], permission: { judgment: 'none' } } },
  '/api/notebook-template': { json: { version: 0, sections: [] } },
  ...overrides,
}); }
it('lists three fields with exact completion counts, dates and saved rating; searches and filters', async () => {
  setup(); renderWithClient(<RecordsScreen session={SESSION_DEV as AuthSession} />);
  const list = within(await screen.findByRole('list'));
  expect(list.getByText(RECORDS[0].protocol_title)).toBeInTheDocument();
  expect(screen.getAllByText('끝낸 단계 2개')).toHaveLength(2);
  expect(screen.getByText('평점 4 / 5')).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText('기록 찾기'), '전극');
  expect(list.queryByText(RECORDS[0].protocol_title)).not.toBeInTheDocument();
  expect(list.getByText(RECORDS[2].protocol_title)).toBeInTheDocument();
  await userEvent.clear(screen.getByLabelText('기록 찾기'));
  await userEvent.selectOptions(screen.getByLabelText('상태'), 'stopped');
  expect(list.getByText(RECORDS[1].protocol_title)).toBeInTheDocument();
  expect(list.queryByText(RECORDS[0].protocol_title)).not.toBeInTheDocument();
});
it('shows step timeline, original and corrected words, photo, timer, help and today values without writes', async () => {
  const { calls } = setup(); renderWithClient(<RecordDetailScreen sessionId='rx3-0' />);
  expect(await screen.findByText('고친 메모')).toBeInTheDocument();
  expect(screen.getByText('고침 · 처음 기록: 첫 메모')).toBeInTheDocument();
  expect(await screen.findByText('타이머 시작')).toBeInTheDocument();
  expect(screen.getByText('원문 60 s → 실제 90 s [미확인]')).toBeInTheDocument();
  expect(screen.getByText('침전이 보여요')).toBeInTheDocument();
  expect(screen.getByText('60 s → 90 s')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '사진 원본 내려받기' })).toHaveAttribute('href', '/api/workspace/experiments/rx3-0/evidence/photo-1');
  expect(screen.getByRole('link', { name: '실험 노트 보기' })).toHaveAttribute('href', '#/records/rx3-0/notebook/report-rx3');
  expect(calls.every(c => !c.init?.method || c.init.method === 'GET')).toBe(true);
});
it('renders the server notebook tables as text with unconfirmed labels and unchanged Word URL', async () => {
  setup(); renderWithClient(<NotebookScreen sessionId='rx3-0' reportId='report-rx3' session={SESSION_DEV as AuthSession} />);
  expect(await screen.findByText('원문 60 s → 실제 90 s [미확인]')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Word로 내려받기' })).toHaveAttribute('href', '/api/experiment-reports/report-rx3.docx');
});
it.each([[], null])('shows empty or failed record state with retry', async rows => {
  fakeFetch({ '/api/workspace/experiments': rows ? { json: { experiments: rows } } : { status: 503 }, '/api/workspace/session': { json: {} } });
  renderWithClient(<RecordsScreen session={SESSION_DEV as AuthSession} />);
  if (rows) expect(await screen.findByText('아직 기록이 없어요. 실험을 하면 여기에 남아요.')).toBeInTheDocument();
  else expect(await screen.findByRole('button', { name: '다시 불러오기' })).toBeInTheDocument();
});

it('only the owner can confirm a saved value through the existing review API', async () => {
  const own = { ...NOTEBOOK, report: { ...NOTEBOOK.report, owner_principal_id: 'me' } };
  const { calls } = setup({
    '/api/experiment-reports/report-rx3/notebook': { json: own },
    '/api/experiment-reports/report-rx3/review/value-1': { json: { items: [] } },
  });
  renderWithClient(<NotebookScreen sessionId='rx3-0' reportId='report-rx3' session={SESSION_DEV as AuthSession} />);
  await userEvent.click(await screen.findByRole('button', { name: '확인하기' }));
  expect(calls.find(c => c.init?.method === 'POST')?.path).toBe('/api/experiment-reports/report-rx3/review/value-1');
  expect(JSON.parse(calls.find(c => c.init?.method === 'POST')!.init!.body as string)).toEqual({ action: 'confirm' });
});
it('a same-lab admin can read another owner notebook with no value write control', async () => {
  setup(); renderWithClient(<NotebookScreen sessionId='rx3-0' reportId='report-rx3' session={{ ...SESSION_DEV, account: { ...SESSION_DEV.account, admin: true } } as AuthSession} />);
  expect(await screen.findByRole('button', { name: '확인함 남기기' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '확인하기' })).not.toBeInTheDocument();
});
it('a PDF exporter refusal stays on the notebook with Word as the next action', async () => {
  setup({ '/api/experiment-reports/report-rx3.pdf': { status: 503, json: { detail: 'pdf_unavailable' } } });
  renderWithClient(<NotebookScreen sessionId='rx3-0' reportId='report-rx3' session={SESSION_DEV as AuthSession} />);
  await userEvent.click(await screen.findByRole('link', { name: 'PDF 내려받기' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('PDF를 만들 수 없어요. Word로 내려받아 주세요.');
});

it('a correction sends the persons exact scientific string to the existing review gate', async () => {
  const { calls } = setup({
    '/api/experiment-reports/report-rx3/notebook': { json: { ...NOTEBOOK, report: { ...NOTEBOOK.report, owner_principal_id: 'me' } } },
    '/api/experiment-reports/report-rx3/review/value-1': { json: { items: [] } },
  });
  renderWithClient(<NotebookScreen sessionId='rx3-0' reportId='report-rx3' session={SESSION_DEV as AuthSession} />);
  await userEvent.click(await screen.findByRole('button', { name: '고치기' }));
  const input = screen.getByLabelText('고친 값');
  await userEvent.clear(input); await userEvent.type(input, '1.5 µL');
  await userEvent.click(screen.getByRole('button', { name: '고쳐서 확인하기' }));
  const write = calls.find(call => call.init?.method === 'POST');
  expect(write?.path).toBe('/api/experiment-reports/report-rx3/review/value-1');
  expect(JSON.parse(write!.init!.body as string)).toEqual({ action: 'correct', text: '1.5 µL' });
});
it('an unavailable identity never claims that this is another persons notebook', async () => {
  setup({
    '/api/workspace/session': { status: 503, json: { detail: 'workspace_unavailable' } },
    '/api/experiment-reports/report-rx3/notebook': { json: { ...NOTEBOOK, report: { ...NOTEBOOK.report, owner_principal_id: 'me' } } },
  });
  renderWithClient(<NotebookScreen sessionId='rx3-0' reportId='report-rx3' session={SESSION_DEV as AuthSession} />);
  await screen.findByRole('link', { name: 'Word로 내려받기' }, { timeout: 10000 });
  expect(screen.queryByText('다른 사람의 기록이에요. 보기만 할 수 있어요.')).not.toBeInTheDocument();
  expect(screen.getByText('이 기록은 보기만 할 수 있어요.')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '확인하기' })).not.toBeInTheDocument();
});
it('a failed timeline read never claims there are no records', async () => {
  setup({
    '/api/workspace/experiments/rx3-0/timeline': { status: 503, json: { detail: 'timeline_unavailable' } },
    '/api/workspace/experiments/rx3-0/reports': { json: { reports: [] } },
  });
  renderWithClient(<RecordDetailScreen sessionId='rx3-0' />);
  expect(await screen.findByRole('alert', {}, { timeout: 10000 })).toHaveTextContent('기록을 불러오지 못했어요.');
  expect(screen.queryByText('이 실험에는 아직 남긴 기록이 없어요.')).not.toBeInTheDocument();
});
it('keeps a document attachment distinct from a photo', async () => {
  setup({ '/api/workspace/experiments/rx3-0/timeline': { json: { timeline: [{ event_id: 'doc', event_type: 'evidence_attached', step_label: '1', evidence: { evidence_id: 'document-1', media_type: 'application/pdf', original_filename: 'sample.pdf', caption: '견본 문서' } }] } } });
  renderWithClient(<RecordDetailScreen sessionId='rx3-0' />);
  expect(await screen.findByRole('link', { name: '첨부 파일 내려받기' }, { timeout: 10000 })).toHaveAttribute('href', '/api/workspace/experiments/rx3-0/evidence/document-1');
  expect(screen.queryByRole('img', { name: '견본 문서' })).not.toBeInTheDocument();
});

it('shows the grounded notebook skeleton, deterministic visuals, Methods fields and opt-in comparison', async () => {
  const rich = {
    ...NOTEBOOK,
    report: { ...NOTEBOOK.report, session_id: 'rx3-0', owner_principal_id: 'me' },
    notebook: {
      ...NOTEBOOK.notebook,
      method_rows: [{ label: '1', source_text: 'Mix for 60 s.', actual_values: '60 s → 90 s', started_at: '10:00', ended_at: '10:02', timer: '실제 90초' }],
      timeline: [{ at: '10:00', step_label: '1', kind: 'start', text: '실험 시작' }, { at: '10:01', step_label: '1', kind: 'observation', text: 'A1 well 탁함' }],
      value_bars: [{ label: '1단계 · 시간', kind: '시간', source: '60 s', actual: '90 s', source_number: '60', actual_number: '90', unit: 's' }],
      images: [
        { kind: 'photo', step_label: '1', caption: '배양 사진', url: '/api/workspace/experiments/rx3-0/evidence/photo-note', media_type: 'image/png', asset_id: 'photo-note' },
        { kind: 'source_figure', step_label: '1', caption: 'Figure 1. 원문 plate', url: '/api/protocols/p/revisions/r/figures/source-figure-1-1', media_type: 'image/png', asset_id: 'source-figure-1-1', source_page: 1 },
      ],
      plate_positions: [{ label: 'A1 well', step_label: '1', words: 'A1 well 탁함' }],
      sample_rows: [],
      domain: { code: 'biology', label: '바이오', selected_by: 'source_analysis', fields: [{ label: '세포주', value: 'A549 cells', origin: 'source', source_keys: ['p-1'], formula: '' }, { label: '계대 수', value: '', origin: 'blank', source_keys: [], formula: '' }] },
      inventory: [{ kind: '재료', name: 'MTT reagent', vendor: 'Sigma-Aldrich', catalog_number: 'M5655', lot_number: 'LOT-24', stage: 'after', source_keys: ['inventory-1'] }],
      interpretation_words: '',
      section_template: [
        { id: 'experiment_info', name: '실험 정보' }, { id: 'purpose', name: '목적' },
        { id: 'methods', name: '방법' }, { id: 'observations', name: '관찰·특이사항' },
        { id: 'results', name: '결과' }, { id: 'interpretation_next', name: '해석·다음 할 일' },
        { id: 'domain', name: '분야별 기록' }, { id: 'appendix', name: '부록' },
      ],
    },
  };
  const { calls } = setup({
    '/api/experiment-reports/report-rx3/notebook': { json: rich },
    '/api/experiment-reports/report-rx3/sharing': { json: { report_id: 'report-rx3', shared: false, changed_at: null } },
    '/api/experiment-reports/report-rx3/comparisons': { json: { records: [
      { record_scope: 'mine', label: '내 기록', value_rows: [], method_rows: [], timeline: [], notes: [] },
      { record_scope: 'shared', label: '공유 기록 1', value_rows: [], method_rows: [], timeline: [], notes: [{ step_label: '1', at: '10:03', kind: '메모', text: 'A1 well 침전 있음' }] },
    ], permission: { scope: 'same_organization_same_protocol', other_records: 'opt_in_read_only', withdrawal: 'latest_append_only_event', judgment: 'none' } } },
  });
  renderWithClient(<NotebookScreen sessionId='rx3-0' reportId='report-rx3' session={SESSION_DEV as AuthSession} />);
  expect(await screen.findByRole('heading', { name: '방법' })).toBeInTheDocument();
  expect(screen.getByText('Mix for 60 s.')).toBeInTheDocument();
  expect(screen.getByText('A549 cells')).toBeInTheDocument();
  expect(screen.getByText('LOT-24')).toBeInTheDocument();
  expect(screen.getByRole('img', { name: '1단계 · 배양 사진' })).toHaveAttribute('src', '/api/workspace/experiments/rx3-0/evidence/photo-note');
  expect(screen.getByRole('img', { name: '1단계 · Figure 1. 원문 plate' })).toBeInTheDocument();
  expect(screen.getByTestId('notebook-value-bar')).toBeInTheDocument();
  expect(await screen.findByText((_, node) => node?.tagName === 'P' && Boolean(node.textContent?.includes('A1 well 침전 있음')))).toBeInTheDocument();
  await userEvent.click(await screen.findByRole('button', { name: '이 기록 비교 공유 켜기' }));
  const share = calls.find(call => call.path.endsWith('/sharing') && call.init?.method === 'POST');
  expect(JSON.parse(share!.init!.body as string)).toEqual({ shared: true });
});

it('lets only a lab admin rename and reorder the fixed notebook sections', async () => {
  const sections = [
    { id: 'experiment_info', name: '실험 정보' }, { id: 'purpose', name: '목적' },
    { id: 'methods', name: '방법' }, { id: 'observations', name: '관찰·특이사항' },
    { id: 'results', name: '결과' }, { id: 'interpretation_next', name: '해석·다음 할 일' },
    { id: 'domain', name: '분야별 기록' }, { id: 'appendix', name: '부록' },
  ];
  const { calls } = setup({
    '/api/notebook-template': { json: { version: 0, sections } },
    '/api/experiment-reports/report-rx3/notebook': { json: { ...NOTEBOOK, report: { ...NOTEBOOK.report, owner_principal_id: 'other' } } },
  });
  renderWithClient(<NotebookScreen sessionId='rx3-0' reportId='report-rx3' session={{ ...SESSION_DEV, account: { ...SESSION_DEV.account, admin: true } } as AuthSession} />);
  const names = await screen.findAllByLabelText('노트 절 이름');
  await userEvent.clear(names[0]); await userEvent.type(names[0], '기본 정보');
  await userEvent.click(screen.getByRole('button', { name: '목적 위로' }));
  await userEvent.click(screen.getByRole('button', { name: '연구실 노트 양식 저장' }));
  const write = calls.find(call => call.path === '/api/notebook-template' && call.init?.method === 'PUT');
  expect(JSON.parse(write!.init!.body as string).sections.slice(0, 2)).toEqual([
    { id: 'purpose', name: '목적' }, { id: 'experiment_info', name: '기본 정보' },
  ]);
});
