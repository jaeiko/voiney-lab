import type { Page } from '@playwright/test';

// Lane RX0 (2026-10-24): the new screen at /app/ -- a React page that reads
// the same server routes as the old one. Server answers are mocked with
// page.route where a walk needs a state the CI server (dev mode, empty
// catalog) does not have; the login walk mocks the session as accounts mode.

export const APP = '/app/';

export const ENTRY_READY = {
  protocol_id: 'p-rx0-ready', title: 'In-gel digestion', source_filename: 'in-gel-digestion.pdf', source_sha256: 'a'.repeat(64),
  revision_id: 'pdf-1', readiness_status: 'guidance_ready', analysis_status: 'structured_analysis_ready', step_count: 9,
  created_at: '2026-10-19T01:00:00+00:00', available_for_execution: true, development_only: false, lifecycle_state: 'ready',
  execution_blocker_codes: [], analysis_outdated_codes: [], analysis_outdated_ko: null, review_pending: null, review_versions: [], current_review_version: null,
};
export const ENTRY_REVIEW = {
  ...ENTRY_READY, protocol_id: 'p-rx0-review', title: 'Western blot', source_filename: 'western.pdf', available_for_execution: false,
  analysis_status: 'review_pending', lifecycle_state: 'review_pending', readiness_status: 'analysis_required', step_count: null,
  created_at: '2026-10-22T01:00:00+00:00', review_pending: { needs_review_count: 8, by_reason: {}, failed_pages: [], message_ko: '검토 대기 · 확인 필요 8건' },
};
export const ENTRY_ANALYSING = {
  ...ENTRY_READY, protocol_id: 'p-rx0-analysing', title: 'RNA extraction', source_filename: 'rna.docx', available_for_execution: false,
  analysis_status: 'analyzing', lifecycle_state: 'analyzing', readiness_status: 'analysis_required', step_count: null, created_at: '2026-10-24T01:00:00+00:00',
};
export const ENTRY_FAILED = {
  ...ENTRY_READY, protocol_id: 'p-rx0-failed', title: 'PCR', source_filename: 'pcr.pdf', available_for_execution: false,
  analysis_status: 'analysis_failed', lifecycle_state: 'analysis_failed', readiness_status: 'analysis_required', step_count: null, created_at: '2026-10-23T01:00:00+00:00',
};
export const ACCOUNT = { account_id: 'acc-rx0', name: '재준', organization_id: 'lab-a', admin: false, entitlement: null, disabled: false };

export async function mockCatalog(page: Page, protocols: object[], experiments: object[] = []) {
  await page.route('**/api/protocols', (route) => route.fulfill({ json: { protocols } }));
  await page.route('**/api/protocols/*', (route) => {
    const id = decodeURIComponent(route.request().url().split('/api/protocols/')[1].split('?')[0]);
    const entry = protocols.find((item) => (item as { protocol_id: string }).protocol_id === id);
    if (!entry) return route.fulfill({ status: 404, json: { detail: 'protocol_not_found' } });
    return route.fulfill({ json: { ...entry, analysis_run: { state: 'analyzing', requested_at: new Date(Date.now() - 3 * 60_000).toISOString(), total_chunks: 1, completed_chunks: 0, reanalysis: false, failure_code: null } } });
  });
  await page.route('**/api/workspace/experiments', (route) => route.fulfill({ json: { experiments } }));
  await page.route('**/api/workspace/session', (route) => route.fulfill({ json: { principal_id: 'acc-rx0', display_name: '재준', organization_id: 'lab-a' } }));
}

export async function mockAccounts(page: Page, signedIn: boolean) {
  await page.route('**/api/auth/session', (route) =>
    route.fulfill({ json: signedIn ? { mode: 'accounts', signed_in: true, account: ACCOUNT } : { mode: 'accounts', signed_in: false, account: null } }),
  );
}
