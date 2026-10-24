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
