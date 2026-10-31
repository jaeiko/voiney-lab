import { expect, test } from '@playwright/test';

import { NOTEBOOK, RECORDS } from '../../web/src/test/records';

const reports = [
  { report_id: 'report-rx3', session_id: 'rx3-0', protocol_id: 'source-0', protocol_title: '세포 염색 견본', status: 'completed', started_at: '2026-10-25T01:00:00Z', ended_at: '2026-10-25T02:00:00Z' },
  { report_id: 'report-rx3-2', session_id: 'rx3-2', protocol_id: 'source-2', protocol_title: '전극 코팅 견본', status: 'completed', started_at: '2026-10-27T01:00:00Z', ended_at: '2026-10-27T02:00:00Z' },
];

const binder = {
  binder_id: 'NB-NOTE2', topic_name: '막 조건 검토', date_from: '', date_to: '',
  protocol_id: '', created_at: '2026-10-30T01:00:00Z',
  audit: { ungrounded_numbers: 0, cross_experiment_mixing: 0 },
  entries: reports.map((report, index) => ({
    ...report,
    notebook: {
      ...NOTEBOOK.notebook,
      title: report.protocol_title,
      method_rows: [{
        label: '1', text_ko: index ? '전극을 코팅한다.' : '세포를 섞는다.',
        source_text: index ? 'Coat the electrode.' : 'Mix the cells.',
        translation_status: 'passed', translation_source: 'checked_translation',
        actual_values: '', started_at: '10:00', ended_at: '10:10', timer: '',
      }],
      observations: [{
        label: '1', at: '10:10', kind: '관찰',
        text: index ? '전극만의 관찰' : '세포만의 관찰',
        shown: index ? '전극만의 관찰' : '세포만의 관찰',
        state: { code: 'read_back', label: '되읽음', original: '' },
      }],
      timeline: [{ at: '10:10', step_label: '', kind: 'completed', text: '실험 완료' }],
    },
    continuation: index ? { source_report_id: 'report-rx3', source_session_id: 'rx3-0' } : null,
    condition_revisions: index ? [{
      label: '연구실판 v1', recorded_at: '2026-10-27T00:50:00Z',
      changes: ['2단계 · 80 µL → 90 µL'],
    }] : [],
  })),
};

test('NOTE2 creates and reads one isolated notebook binder on the new screen', async ({ page }) => {
  const writes: unknown[] = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/auth/session') return route.fulfill({ json: { mode: 'dev', signed_in: true, account: { account_id: 'me', name: '가짜 연구자', organization_id: 'lab-demo', admin: false } } });
    if (path === '/api/workspace/session') return route.fulfill({ json: { principal_id: 'me' } });
    if (path === '/api/protocols') return route.fulfill({ json: { protocols: [] } });
    if (path === '/api/workspace/experiments') return route.fulfill({ json: { experiments: RECORDS } });
    if (path === '/api/notebook-binders/candidates') return route.fulfill({ json: { reports, binders: [] } });
    if (path === '/api/notebook-binders' && request.method() === 'POST') {
      writes.push(request.postDataJSON());
      return route.fulfill({ status: 201, json: binder });
    }
    if (path === '/api/notebook-binders/NB-NOTE2') return route.fulfill({ json: binder });
    return route.fulfill({ status: 404, json: { detail: 'not_found' } });
  });

  await page.goto('/app/#/records');
  await page.getByLabel('연구 주제 이름').fill('막 조건 검토');
  await page.getByRole('checkbox').nth(0).check();
  await page.getByRole('checkbox').nth(1).check();
  await page.getByRole('button', { name: '묶음 노트 만들기' }).click();
  await expect(page.getByRole('link', { name: '막 조건 검토 열기' })).toBeVisible();
  expect(writes).toEqual([{
    topic_name: '막 조건 검토', date_from: '', date_to: '', protocol_id: '',
    report_ids: ['report-rx3', 'report-rx3-2'],
  }]);

  await page.getByRole('link', { name: '막 조건 검토 열기' }).click();
  await expect(page.getByTestId('binder-screen')).toContainText('세포만의 관찰');
  await expect(page.getByTestId('binder-screen')).toContainText('전극만의 관찰');
  await expect(page.getByTestId('binder-screen')).toContainText('저장 지점에서 이어서 시작');
  await expect(page.getByTestId('binder-screen')).toContainText('연구실판 v1');
  await expect(page.getByRole('link', { name: '묶음 Word 내려받기' })).toHaveAttribute('href', '/api/notebook-binders/NB-NOTE2.docx');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
});
