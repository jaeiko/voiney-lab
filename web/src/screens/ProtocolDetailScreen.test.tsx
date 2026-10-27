import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from '../App';
import { fakeFetch, renderWithClient, SESSION_DEV, ENTRY_READY } from '../test/helpers';

afterEach(() => { window.location.hash = ''; vi.unstubAllGlobals(); });
it('owns the full detail route, source evidence, reagent cells, and append-only version changes without writing on read', async () => {
  window.location.hash = '#/protocols/p-ready';
  const { calls } = fakeFetch({
    '/api/auth/session': { json: SESSION_DEV },
    '/api/protocols/p-ready/review': { json: { ...ENTRY_READY, source: { filename: 'sample.pdf', page_count: 2 }, materials: [{ name_source_text: 'NaCl', quantities: [{ source_text: '0.15 M' }] }], equipment: [{ name_source_text: 'balance' }], sections: [{ title_source_text: 'Preparation', steps: [{ step_id: 's1', source_label: '1', instruction_source_text: 'Add 0.15 M NaCl.', evidence: { source_page_number: 2, source_excerpt: 'Add 0.15 M NaCl.' }, sub_actions: [{ instruction_source_text: 'Mix.', conditions: [{ source_text: 'room temperature' }] }] }] }], review_versions: [{ version: 1, version_ko: '검토본 v1', reviewer_name: '견본' }] } },
    '/api/protocols/p-ready/reagent-tables': { json: { reagent_tables: [{ table_id: 't1', filename: 'sample.xlsx', sheets: [{ name: 'Sheet1', cells: [{ row: 1, column: 1, display: '0.15 M', value: '0.15 M' }] }] }] } },
    '/api/protocols/p-ready/review-versions/1': { json: { version: 1, reviewer_name: '견본', changes: [{ kind_ko: '시간', original: '10 min', reviewed: '12 min', origin: 'reviewer_input', decided_by: '견본' }] } },
  });
  renderWithClient(<App />);
  expect(await screen.findByTestId('protocol-detail')).toBeInTheDocument();
  expect(await screen.findByText('NaCl')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: '확인 화면 열기' })).toHaveAttribute('href', '#/protocols/p-ready/review');
  expect(screen.getByText('room temperature')).toBeInTheDocument();
  expect(screen.getAllByText('Add 0.15 M NaCl.').length).toBeGreaterThan(0);
  expect(screen.getByRole('link', { name: '원문 2쪽 열기' })).toHaveAttribute('href', '/api/protocols/p-ready/review-draft/source-pages/2/image');
  await userEvent.click(screen.getByRole('button', { name: /검토본 v1/ }));
  expect(await screen.findByText('10 min → 12 min')).toBeInTheDocument();
  expect(screen.getByText('직접 고친 값')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText('0.15 M', { selector: 'td' })).toBeInTheDocument());
  expect(calls.every(call => !call.init?.method || call.init.method === 'GET')).toBe(true);
});
it('requests a new analysis of a passed protocol only after choosing its page range', async () => {
 window.location.hash = '#/protocols/p-ready';
 const { calls } = fakeFetch({ '/api/auth/session': { json: SESSION_DEV }, '/api/protocols/p-ready/review': { json: { ...ENTRY_READY, source: { page_count: 3 }, sections: [] } }, '/api/protocols/p-ready/reagent-tables': { json: { reagent_tables: [] } }, '/api/protocols/p-ready/reanalysis': { status: 202, json: {} } });
 renderWithClient(<App />);
 await userEvent.click(await screen.findByRole('button', { name: '쪽 고르기' }));
 await userEvent.click(screen.getByRole('button', { name: '2쪽' }));
 await userEvent.click(screen.getByRole('button', { name: '3쪽' }));
 expect(calls.some(c => c.path.includes('/reanalysis'))).toBe(false);
 await userEvent.click(screen.getByTestId('pages-start'));
 await waitFor(() => expect(calls.some(c => c.path === '/api/protocols/p-ready/reanalysis?first_page=2&last_page=3')).toBe(true));
});

it('shows a failed detail load with retry and keeps scientific strings that resemble internal step ids verbatim', async () => {
 window.location.hash = '#/protocols/p-ready';
 let failed = true;
 fakeFetch({
  '/api/auth/session': { json: { mode: 'accounts', signed_in: true, account: { account_id: 'reader', name: '견본', organization_id: 'lab', admin: false } } },
  '/api/protocols/p-ready/review': () => failed ? { status: 503 } : { json: { ...ENTRY_READY, sections: [{ steps: [{ step_id: 's1', source_label: '1', instruction_source_text: 's1' }] }], materials: [{ name_source_text: 's1' }] } },
  '/api/protocols/p-ready/reagent-tables': { json: { reagent_tables: [] } },
 });
 renderWithClient(<App />);
 expect(await screen.findByRole('alert')).toHaveTextContent('상세를 불러오지 못했어요');
 failed = false;
 await userEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
 expect((await screen.findAllByText('s1')).length).toBe(2);
 expect(screen.queryByText('개발자 정보')).toBeNull();
 expect(screen.getByText('아직 등록한 확인 버전이 없어요.')).toBeInTheDocument();
 expect(screen.getByText('붙인 시약 표가 없어요.')).toBeInTheDocument();
});
it('rejects unsupported table input and reports a duplicate workbook through the existing upload API', async () => {
 window.location.hash = '#/protocols/p-ready';
 const { calls } = fakeFetch({ '/api/auth/session': { json: SESSION_DEV }, '/api/protocols/p-ready/review': { json: { ...ENTRY_READY, sections: [] } }, '/api/protocols/p-ready/reagent-tables': init => init?.method === 'POST' ? { json: { deduplicated: true } } : { json: { reagent_tables: [] } } });
 renderWithClient(<App />);
 const input = await screen.findByLabelText('시약 표(.xlsx) 붙이기');
 const user = userEvent.setup({ applyAccept: false });
 await user.upload(input, new File(['fake'], 'bad.pdf', { type: 'application/pdf' }));
 expect(await screen.findByText('엑셀 .xlsx 파일을 골라 주세요.')).toBeInTheDocument();
 expect(calls.filter(c => c.init?.method === 'POST')).toHaveLength(0);
 await user.upload(input, new File(['fake'], 'sample.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }));
 expect(await screen.findByText('이미 붙인 표예요.')).toBeInTheDocument();
 expect(calls.filter(c => c.init?.method === 'POST')).toHaveLength(1);
});
