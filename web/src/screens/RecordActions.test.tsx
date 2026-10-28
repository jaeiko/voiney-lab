import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { RecordDetailScreen, NotebookScreen } from './RecordsScreen';
import { fakeFetch, renderWithClient, SESSION_DEV } from '../test/helpers';
import { NOTEBOOK } from '../test/records';

afterEach(() => { vi.unstubAllGlobals(); window.location.hash = ''; });
it('adds a typed note and creates a paused checkpoint run only after explicit confirmation with the server version', async () => {
 const { calls } = fakeFetch({
  '/api/workspace/session': { json: { principal_id: 'me' } },
  '/api/workspace/experiments/old': { json: { session_id: 'old', owner_principal_id: 'me', protocol_id: 'p1', status: 'stopped', version: 7 } },
  '/api/workspace/experiments/old/timeline': { json: { session: { version: 7 }, timeline: [], recovery: { checkpoints: [{ step_id: 's2', step_label: '2', carried_step_count: 1 }] } } },
  '/api/workspace/experiments/old/reports': { json: { reports: [] } },
  '/api/workspace/experiments/old/observations': { json: {} },
  '/api/workspace/experiments/old/checkpoint-restart': { json: { session_id: 'new', protocol_id: 'p1', status: 'paused' } },
 });
 renderWithClient(<RecordDetailScreen sessionId='old' />);
 await userEvent.type(await screen.findByLabelText('메모 내용'), '합성 관찰');
 await userEvent.selectOptions(screen.getByLabelText('메모 종류'), 'deviation');
 await userEvent.click(screen.getByRole('button', { name: '메모 남기기' }));
 await waitFor(() => expect(calls.some(c => c.path.endsWith('/observations'))).toBe(true));
 expect(JSON.parse(String(calls.find(c => c.path.endsWith('/observations'))?.init?.body))).toMatchObject({ category: 'deviation', content: '합성 관찰' });
 await userEvent.selectOptions(screen.getByLabelText('저장해 둔 지점'), 's2');
 await userEvent.click(screen.getByRole('button', { name: '이 지점부터 새로 시작하기' }));
 expect(calls.some(c => c.path.endsWith('/checkpoint-restart'))).toBe(false);
 await userEvent.click(screen.getByRole('button', { name: '새 실험 만들기' }));
 await waitFor(() => expect(window.location.hash).toBe('#/start/p1?resume=new'));
 expect(JSON.parse(String(calls.find(c => c.path.endsWith('/checkpoint-restart'))?.init?.body))).toEqual({ checkpoint_step_id: 's2', expected_version: 7 });
});
it('shows preparation state, regenerates prose on request and refreshes the notebook after completion', async () => {
 let generated = false;
 const { calls } = fakeFetch({
  '/api/workspace/session': { json: { principal_id: 'me' } },
  '/api/experiment-reports/r/notebook': { json: { ...NOTEBOOK, report: { ...NOTEBOOK.report, session_id: 's', owner_principal_id: 'me' } } },
  '/api/experiment-reports/r/prose': (init) => { if (init?.method === 'POST') generated = true; return { json: { state: 'ready', label: generated ? '문장을 다시 준비했어요.' : '문장이 준비됐어요.' } }; },
 });
 renderWithClient(<NotebookScreen sessionId='s' reportId='r' session={{ ...SESSION_DEV, mode: "dev" }} />);
 expect(await screen.findByText('문장이 준비됐어요.')).toBeInTheDocument();
 await userEvent.click(screen.getByRole('button', { name: '보고서 문장 다시 만들기' }));
 expect(await screen.findByText('문장을 다시 준비했어요.')).toBeInTheDocument();
 expect(calls.filter(c => c.path.endsWith('/prose') && c.init?.method === 'POST')).toHaveLength(1);
 expect(calls.filter(c => c.path.endsWith('/notebook')).length).toBeGreaterThan(1);
});

it('keeps another owners notebook read-only and shows report preparation even before a notebook exists', async () => {
 const { calls } = fakeFetch({
  '/api/workspace/session': { json: { principal_id: 'me' } },
  '/api/experiment-reports/r/notebook': { json: { ...NOTEBOOK, notebook: null, report: { ...NOTEBOOK.report, session_id: 's', owner_principal_id: 'other' } } },
  '/api/experiment-reports/r/prose': { json: { state: 'preparing', label: '보고서 문장을 준비하고 있어요.' } },
 });
 renderWithClient(<NotebookScreen sessionId='s' reportId='r' session={{ ...SESSION_DEV, mode: 'dev' }} />);
 expect(await screen.findByText('보고서 문장을 준비하고 있어요.')).toBeInTheDocument();
 expect(screen.queryByRole('button', { name: '보고서 문장 다시 만들기' })).toBeNull();
 expect(calls.every(c => !c.init?.method || c.init.method === 'GET')).toBe(true);
});
it('a checkpoint conflict preserves the record and explains how to retry', async () => {
 const { calls } = fakeFetch({
  '/api/workspace/session': { json: { principal_id: 'me' } },
  '/api/workspace/experiments/old': { json: { session_id: 'old', owner_principal_id: 'me', protocol_id: 'p1', status: 'stopped', version: 7 } },
  '/api/workspace/experiments/old/timeline': { json: { session: { version: 7 }, timeline: [], recovery: { checkpoints: [{ step_id: 's2', step_label: '2', carried_step_count: 1 }] } } },
  '/api/workspace/experiments/old/reports': { json: { reports: [] } },
  '/api/workspace/experiments/old/checkpoint-restart': { status: 409, json: { detail: 'experiment_version_conflict' } },
 });
 renderWithClient(<RecordDetailScreen sessionId='old' />);
 await userEvent.selectOptions(await screen.findByLabelText('저장해 둔 지점'), 's2');
 await userEvent.click(screen.getByRole('button', { name: '이 지점부터 새로 시작하기' }));
 await userEvent.click(screen.getByRole('button', { name: '새 실험 만들기' }));
 expect(await screen.findByText('이 지점에서 시작하지 못했어요. 기록을 새로 불러온 뒤 지점을 다시 골라 주세요.')).toBeInTheDocument();
 expect(window.location.hash).toBe('');
 expect(calls.filter(c => c.init?.method === 'POST')).toHaveLength(1);
});
