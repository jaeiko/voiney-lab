import { test as base } from '@playwright/test';

// Lane IA decision ① / ⑦ (2026-10-19): the page is five tabs by hash route --
// #/home, #/protocols (#/protocols/<id>), #/upload, #/experiment, #/records
// (#/records/<id>), #/settings. The one page behind "자세히 보기" is gone, so a
// spec opens the tab its parts live on. The experiment tab shows lane ON's
// wizard before a run, lane UI0's run view during it (the page moves there by
// itself when the server's state starts a run) and the record after it.
export const TAB = {
  home: '/#/home',
  protocols: '/#/protocols',
  upload: '/#/upload',
  experiment: '/#/experiment',
  records: '/#/records',
  settings: '/#/settings',
} as const;

export const protocolRoute = (protocolId: string) => `/#/protocols/${encodeURIComponent(protocolId)}`;
export const recordRoute = (sessionId: string) => `/#/records/${encodeURIComponent(sessionId)}`;

export const test = base;
export { expect, devices } from '@playwright/test';
export type { Page, Route } from '@playwright/test';
