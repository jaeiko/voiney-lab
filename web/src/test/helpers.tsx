import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

export function testClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false, refetchOnWindowFocus: false } } });
}

export function renderWithClient(ui: ReactElement, client: QueryClient = testClient()): RenderResult & { client: QueryClient } {
  const result = render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  return Object.assign(result, { client });
}

export type Answer = { status?: number; json?: unknown; headers?: Record<string, string> } | Error;

/** A fetch that answers each path from a table; a path not in it is a 404. */
export function fakeFetch(table: Record<string, Answer | ((init?: RequestInit) => Answer)>) {
  const calls: Array<{ path: string; init?: RequestInit }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = typeof input === "string" ? input : input instanceof URL ? input.pathname + input.search : input.url;
    calls.push({ path, init });
    const key = Object.keys(table).find((candidate) => path === candidate || path.startsWith(`${candidate}?`));
    const entry = key ? table[key] : undefined;
    const answer = typeof entry === "function" ? entry(init) : entry;
    if (answer instanceof Error) throw answer;
    const status = answer?.status ?? (answer ? 200 : 404);
    const body = answer?.json === undefined ? (answer ? "" : JSON.stringify({ detail: "not_found" })) : JSON.stringify(answer.json);
    return new Response(body, { status, headers: { "Content-Type": "application/json", ...(answer?.headers ?? {}) } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

export const ENTRY_READY = {
  protocol_id: "p-ready",
  title: "In-gel digestion",
  source_filename: "in-gel-digestion.pdf",
  revision_id: "pdf-1",
  analysis_status: "structured_analysis_ready",
  lifecycle_state: "ready",
  step_count: 9,
  created_at: "2026-10-19T01:00:00+00:00",
  available_for_execution: true,
  execution_blocker_codes: [],
  development_only: false,
};

export const ENTRY_REVIEW = {
  protocol_id: "p-review",
  title: "Western blot",
  source_filename: "western.pdf",
  analysis_status: "review_pending",
  lifecycle_state: "review_pending",
  step_count: null,
  created_at: "2026-10-22T01:00:00+00:00",
  available_for_execution: false,
  review_pending: { needs_review_count: 2, message_ko: "검토 대기 · 확인 필요 2건" },
};

export const ENTRY_ANALYSING = {
  protocol_id: "p-analysing",
  title: "RNA extraction",
  source_filename: "rna.docx",
  analysis_status: "analyzing",
  lifecycle_state: "analyzing",
  created_at: "2026-10-24T01:00:00+00:00",
  available_for_execution: false,
};

export const ENTRY_FAILED = {
  protocol_id: "p-failed",
  title: "PCR",
  source_filename: "pcr.pdf",
  analysis_status: "analysis_failed",
  lifecycle_state: "analysis_failed",
  created_at: "2026-10-23T01:00:00+00:00",
  available_for_execution: false,
};

export const SESSION_ACCOUNTS = {
  mode: "accounts",
  signed_in: true,
  account: { account_id: "a1", name: "재준", organization_id: "lab-a", admin: false },
};
export const SESSION_DEV = { mode: "dev", signed_in: true, account: { account_id: "dev", name: "개발 로그인", organization_id: "dev", admin: false } };
export const SESSION_OUT = { mode: "accounts", signed_in: false, account: null };

// --- Lane RX1 fixtures: an upload's answer, a review draft with cards, a source page, today's values ---

export const ENTRY_UPLOADED = {
  protocol_id: "p-new",
  title: "표지 최종 하리",
  source_filename: "western-blot.pdf",
  analysis_status: "structured_analysis_ready",
  lifecycle_state: "uploaded",
  step_count: 0,
  created_at: "2026-10-25T01:00:00+00:00",
  available_for_execution: false,
  review_pending: null,
};

export const UPLOAD_ANSWER = { protocol: ENTRY_UPLOADED, deduplicated: false, ocr: { state: "not_required", automatic: false } };

export const SOURCE_PAGES = { protocol_id: "p-new", page_count: 4, thumbnail_dpi: 36, image_dpi: 120 };

export const SOURCE_PAGE_1 = {
  protocol_id: "p-review",
  page: 1,
  page_count: 2,
  ocr: false,
  image_dpi: 120,
  lines: [
    { number: 1, text: "Protocol Gamma", has_value: false, unclear: false, box: [72, 52, 150, 64] },
    { number: 2, text: "Section preparation", has_value: false, unclear: false, box: [72, 66, 170, 78] },
    { number: 3, text: "1. Add 2 mL of buffer A.", has_value: true, unclear: false, box: [72, 80, 200, 92] },
    { number: 4, text: "2. Incubate for 10 min at 37 C.", has_value: true, unclear: false, box: [72, 94, 230, 106] },
    { number: 5, text: "Wear gloves.", has_value: false, unclear: false, box: [72, 108, 140, 120] },
  ],
};

const choice = (choice_id: string, label_ko: string, request: Record<string, unknown> | null, extra: Record<string, unknown> = {}) => ({
  choice_id,
  label_ko,
  request,
  needs: null,
  route: null,
  primary: false,
  ...extra,
});

export const CARD_EVIDENCE = {
  card_id: "step:s2",
  item_id: "step:s2",
  position: 1,
  total: 2,
  progress_ko: "1 / 2",
  type: "evidence",
  kind: "step",
  kind_ko: "단계",
  step_label: "2",
  question_ko: "이 단계의 근거를 원문에서 찾지 못했어요. 원문의 어느 줄인가요?",
  reasons_ko: ["원문에 없음"],
  shown: "2. Incubate for 10 min at 37 C.",
  source: { page: 1, excerpt: "2. Incubate for 10 min at 37 C.", verified: false, lines: null, suggested_lines: { page: 1, first_line: 4, last_line: 4, unique: false } },
  choices: [
    choice("confirm_lines", "고른 줄이 맞아요", { item_id: "step:s2", action: "confirm" }, { needs: "source_lines", primary: true }),
    choice("fix", "직접 고칠게요", { item_id: "step:s2", action: "fix" }, { needs: "source_lines_and_text" }),
    choice("reanalysis", "다시 분석", null, { route: "reanalysis" }),
  ],
  proposal: null,
  change: null,
  readings: null,
  steps: null,
  memo_allowed: true,
  decided: false,
  decision: null,
  current: "2. Incubate for 10 min at 37 C.",
};

export const CARD_CLAIM = {
  card_id: "value:s4/quantities/0",
  item_id: "value:s4/quantities/0",
  position: 2,
  total: 2,
  progress_ko: "2 / 2",
  type: "claim",
  kind: "value",
  kind_ko: "값",
  step_label: "4",
  question_ko: "이 값이 원문과 같나요?",
  reasons_ko: [],
  shown: "500 x g",
  source: { page: 2, excerpt: "4. Spin at 500 x g for 5 min.", verified: true, lines: { page: 2, first_line: 3, last_line: 3 }, suggested_lines: null },
  choices: [
    choice("confirm", "맞아요", { item_id: "value:s4/quantities/0", action: "confirm" }, { primary: true }),
    choice("fix", "직접 고칠게요", { item_id: "value:s4/quantities/0", action: "fix" }, { needs: "text" }),
    choice("clear", "원문에 없어요", { item_id: "value:s4/quantities/0", action: "clear" }),
  ],
  proposal: null,
  change: null,
  readings: null,
  steps: null,
  memo_allowed: true,
  decided: false,
  decision: null,
  current: "500 x g",
};

export const REVIEW_TARGET = { kind: "review_pending", revision_id: "pdf-1", analysis_id: "analysis-1" };

export const REVIEW_DRAFT = {
  protocol_id: "p-review",
  title: "Western blot",
  source_filename: "western.pdf",
  analysis_status: "review_pending",
  available_for_execution: false,
  review_pending: { needs_review_count: 2 },
  target: REVIEW_TARGET,
  items: [],
  cards: [CARD_EVIDENCE, CARD_CLAIM],
  next_card: 0,
  needs_review: ["step:s2", "value:s4/quantities/0"],
  counts: { items: 9, needs_review: 2, decided: 0, undecided: 2, changed: 0 },
  pages: [],
  registrable: false,
  registration_blockers: [],
};

export const RUN_VALUES = [
  { item_id: "s2#1", step_id: "s2", step_label: "2", kind: "time", kind_ko: "시간", number: "10", unit: "min", written: "10 min", shown_ko: "10분", units_ko: "초·분·시간", start: 16, end: 22 },
  { item_id: "s2#2", step_id: "s2", step_label: "2", kind: "temperature", kind_ko: "온도", number: "37", unit: "°C", written: "37 C", shown_ko: "37°C", units_ko: "°C", start: 26, end: 30 },
  { item_id: "s4#1", step_id: "s4", step_label: "4", kind: "speed", kind_ko: "속도", number: "500", unit: "×g", written: "500 x g", shown_ko: "500 ×g", units_ko: "×g", start: 11, end: 18 },
  { item_id: "s4#2", step_id: "s4", step_label: "4", kind: "time", kind_ko: "시간", number: "5", unit: "min", written: "5 min", shown_ko: "5분", units_ko: "초·분·시간", start: 23, end: 28 },
];

export const RUN_OPTIONS = {
  protocol_id: "p-ready",
  target: { kind: "analysis", revision_id: "pdf-1-analysis-1", analysis_id: "analysis-1" },
  revision_id: "pdf-1-analysis-1",
  values: RUN_VALUES,
  limit: 12,
  steps: [
    { step_id: "s1", step_label: "1", text: "1. Add 2 mL of buffer A.", text_ko: "1. 완충액 A 2 mL를 넣어요.", value_ids: [] },
    { step_id: "s2", step_label: "2", text: "2. Incubate for 10 min at 37 C.", text_ko: "2. 37 °C에서 10분 동안 둡니다.", value_ids: ["s2#1", "s2#2"] },
    { step_id: "s3", step_label: "3", text: "3. Wash the pellet twice.", text_ko: null, value_ids: [] },
    { step_id: "s4", step_label: "4", text: "4. Spin at 500 x g for 5 min.", text_ko: null, value_ids: ["s4#1", "s4#2"] },
  ],
  last_time: null,
};
