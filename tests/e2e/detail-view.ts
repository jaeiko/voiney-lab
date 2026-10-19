import { test as base } from '@playwright/test';

// Lane ON stage ② (2026-10-18): the page now opens on the start wizard (one
// thing per screen). The specs written before it drive the 실험 전 view as it
// was, which stays behind "자세히 보기" -- the same choice a person makes, kept
// for the tab in sessionStorage. This fixture makes that choice before each
// page of those specs loads, so they test the view they were written for.
export async function chooseDetailView(target: { addInitScript: (script: () => void) => Promise<void> }) {
  await target.addInitScript(() => {
    try { sessionStorage.setItem('voiney-lab.start-view', 'detail'); } catch (_) { /* no storage: the wizard shows */ }
  });
}

export const test = base.extend({
  page: async ({ page }, use) => {
    await chooseDetailView(page);
    await use(page);
  },
});

export { expect, devices } from '@playwright/test';
export type { Page, Route } from '@playwright/test';
