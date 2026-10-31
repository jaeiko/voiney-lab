import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';

import { RecordsScreen, RecordDetailScreen } from './RecordsScreen';
import { fakeFetch, renderWithClient, SESSION_DEV } from '../test/helpers';
import { REPORT, TIMELINE } from '../test/records';
import type { AuthSession } from '../api/types';

// Lane UI1 (person's test of 2026-10-31): the records list shows when each run started in the lab's zone and the
// notebook's form (never "UTC"), in the server's order (newest run first), and a badge for the values still to confirm.
// Synthetic records of three fields (biology, chemistry, battery materials).

afterEach(() => vi.unstubAllGlobals());

const LIST = [
  // Made on 10/28 and taken up on 10/31: the server sends its run's start first.
  { session_id: 'ui1-bio', protocol_id: 'p-bio', protocol_title: 'In-gel digestion', status: 'stopped', completed_step_count: 2,
    started_at: '2026-10-28T07:38:40Z', run_started_at: '2026-10-31T04:40:22Z', unconfirmed_count: 2, report_status: 'stopped',
    owner_principal_id: 'me', rating: null, rating_state: 'unrated' },
  { session_id: 'ui1-chem', protocol_id: 'p-chem', protocol_title: 'EAM 2.3 Analytical Solution Preparation', status: 'completed', completed_step_count: 13,
    started_at: '2026-10-29T13:45:13Z', run_started_at: '2026-10-29T13:45:25Z', unconfirmed_count: 0, report_status: 'completed',
    owner_principal_id: 'me', rating: 4, rating_state: 'rated' },
  { session_id: 'ui1-battery', protocol_id: 'p-battery', protocol_title: '전극 코팅 견본', status: 'ready', completed_step_count: 0,
    started_at: '2026-10-28T16:38:00Z', run_started_at: '2026-10-28T16:38:00Z', unconfirmed_count: null, report_status: null,
    owner_principal_id: 'me', rating: null, rating_state: 'unrated' },
];

function setup(timezone?: string) {
  return fakeFetch({
    '/api/workspace/experiments': { json: { experiments: LIST, ...(timezone ? { timezone } : {}) } },
    '/api/workspace/session': { json: { principal_id: 'me' } },
    '/api/notebook-binders/candidates': { json: { reports: [], binders: [] } },
  });
}

it('says each run start in Seoul time like the notebook, in the server order, with the unconfirmed badge', async () => {
  setup('Asia/Seoul');
  renderWithClient(<RecordsScreen session={SESSION_DEV as AuthSession} />);
  const cards = await screen.findAllByTestId('record-card');
  expect(cards.map(card => within(card).getByRole('heading').textContent)).toEqual(['In-gel digestion', 'EAM 2.3 Analytical Solution Preparation', '전극 코팅 견본']);
  expect(within(cards[0]).getByText('2026년 10월 31일 (토) 13:40')).toBeInTheDocument();
  expect(within(cards[1]).getByText('2026년 10월 29일 (목) 22:45')).toBeInTheDocument();
  // 16:38 UTC on 10/28 is the next day in Seoul.
  expect(within(cards[2]).getByText('2026년 10월 29일 (목) 01:38')).toBeInTheDocument();
  expect(screen.queryByText(/UTC/)).not.toBeInTheDocument();
  expect(within(cards[0]).getByTestId('record-unconfirmed')).toHaveTextContent('확인 안 된 값 2개');
  expect(within(cards[1]).queryByTestId('record-unconfirmed')).not.toBeInTheDocument();
  expect(within(cards[2]).queryByTestId('record-unconfirmed')).not.toBeInTheDocument();
});

it('filters by the day in the lab zone, and follows another zone the server names', async () => {
  setup('Asia/Seoul');
  renderWithClient(<RecordsScreen session={SESSION_DEV as AuthSession} />);
  await screen.findAllByTestId('record-card');
  await userEvent.type(screen.getByLabelText('시작 날짜부터'), '2026-10-29');
  await userEvent.type(screen.getByLabelText('시작 날짜까지'), '2026-10-29');
  const shown = screen.getAllByTestId('record-card').map(card => within(card).getByRole('heading').textContent);
  expect(shown).toEqual(['EAM 2.3 Analytical Solution Preparation', '전극 코팅 견본']);
});

it('falls back to Seoul when the server names no zone, and uses Berlin when it does', async () => {
  setup();
  const first = renderWithClient(<RecordsScreen session={SESSION_DEV as AuthSession} />);
  expect(await screen.findByText('2026년 10월 31일 (토) 13:40')).toBeInTheDocument();
  first.unmount();
  vi.unstubAllGlobals();
  setup('Europe/Berlin');
  renderWithClient(<RecordsScreen session={SESSION_DEV as AuthSession} />);
  expect(await screen.findByText('2026년 10월 31일 (토) 05:40')).toBeInTheDocument();
});

it('the record detail names the run start and its events by clock time in the lab zone', async () => {
  fakeFetch({
    '/api/workspace/experiments': { json: { experiments: [], timezone: 'Asia/Seoul' } },
    '/api/workspace/session': { json: { principal_id: 'me' } },
    '/api/workspace/experiments/rx3-0': { json: TIMELINE.session },
    '/api/workspace/experiments/rx3-0/timeline': { json: TIMELINE },
    '/api/workspace/experiments/rx3-0/reports': { json: { reports: [REPORT] } },
    '/api/experiment-reports/report-rx3/notebook': { json: { report: REPORT, events: [], review: [], notebook: null } },
  });
  renderWithClient(<RecordDetailScreen sessionId='rx3-0' />);
  expect(await screen.findByTestId('record-detail-when')).toHaveTextContent('2026년 10월 25일 (일) 10:00');
  expect(await screen.findByText('10:01')).toBeInTheDocument();
  expect(screen.queryByText(/UTC/)).not.toBeInTheDocument();
});
