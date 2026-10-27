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
  '/api/experiment-reports/report-rx3/notebook': { json: NOTEBOOK },
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
