import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ENTRY_READY, ENTRY_REVIEW, ENTRY_UPLOADED, fakeFetch, renderWithClient } from "../test/helpers";
import { AnalysisScreen } from "./AnalysisScreen";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("AnalysisScreen", () => {
  it("while the pieces run: the clock, the pieces with their pages, a measured estimate and the leave line", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-10-25T10:10:00Z"), toFake: ["Date"] });
    fakeFetch({
      "/api/protocols/p-new": {
        json: {
          ...ENTRY_UPLOADED,
          analysis_status: "chunk_analysis_in_progress",
          lifecycle_state: "analyzing",
          analysis_run: {
            state: "chunk_analysis_in_progress",
            requested_at: "2026-10-25T10:07:00Z",
            total_chunks: 3,
            completed_chunks: 1,
            chunks: [
              { chunk_id: "piece-1", source_page_start: 1, source_page_end: 15, status: "completed" },
              { chunk_id: "piece-2", source_page_start: 16, source_page_end: 30, status: "in_progress" },
              { chunk_id: "piece-3", source_page_start: 31, source_page_end: 40, status: "pending" },
            ],
          },
        },
      },
    });
    renderWithClient(<AnalysisScreen protocolId="p-new" />);
    const box = await screen.findByTestId("analysis-running");
    expect(box).toHaveTextContent("지난 시간 03:00");
    expect(screen.getByTestId("analysis-pieces")).toHaveTextContent("나눠 읽는 중 1/3");
    expect(box).toHaveTextContent("16–30쪽 · 읽는 중");
    expect(screen.getByTestId("analysis-estimate")).toHaveTextContent("이대로면 약 6분 더 걸려요 (지금까지 속도로 어림)");
    expect(box).toHaveTextContent("기다리는 동안 다른 화면을 봐도 돼요.");
    // The cover-line title gives way to the file's name.
    expect(screen.getByTestId("protocol-title")).toHaveTextContent("western-blot");
    expect(screen.getByText("분석이 끝나야 원문에서 읽은 값으로 안내할 수 있어요.")).toBeInTheDocument();
  });

  it("a single piece with nothing done shows the clock and no made-up estimate", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-10-25T10:10:00Z"), toFake: ["Date"] });
    fakeFetch({ "/api/protocols/p-new": { json: { ...ENTRY_UPLOADED, analysis_status: "analyzing", lifecycle_state: "analyzing", analysis_run: { state: "analyzing", requested_at: "2026-10-25T10:09:30Z", total_chunks: 1, completed_chunks: 0 } } } });
    renderWithClient(<AnalysisScreen protocolId="p-new" />);
    expect(await screen.findByTestId("analysis-running")).toHaveTextContent("지난 시간 00:30");
    expect(screen.queryByTestId("analysis-estimate")).toBeNull();
    expect(screen.queryByTestId("analysis-pieces")).toBeNull();
  });

  it("done and runnable: 실험 시작", async () => {
    fakeFetch({ "/api/protocols/p-ready": { json: { ...ENTRY_READY, analysis_run: { state: "complete" } } } });
    renderWithClient(<AnalysisScreen protocolId="p-ready" />);
    expect(await screen.findByTestId("analysis-ready")).toHaveTextContent("분석이 끝났어요");
    expect(screen.getByTestId("analysis-start")).toHaveAttribute("href", "/index.html#/protocols/p-ready");
  });

  it("done with cards: 확인할 것 N개가 있어요 → 확인하기", async () => {
    fakeFetch({ "/api/protocols/p-review": { json: { ...ENTRY_REVIEW, analysis_run: { state: "review_pending" } } } });
    renderWithClient(<AnalysisScreen protocolId="p-review" />);
    expect(await screen.findByTestId("analysis-review")).toHaveTextContent("확인할 것 2개가 있어요");
    expect(screen.getByTestId("analysis-review-link")).toHaveAttribute("href", "#/protocols/p-review/review");
  });

  it("failed: the reason in plain words and 다시 분석하기 asks the server once more", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch({
      "/api/protocols/p-new": { json: { ...ENTRY_UPLOADED, analysis_status: "analysis_failed", lifecycle_state: "analysis_failed", analysis_run: { state: "failed", failure_code: "provider_timeout" } } },
      "/api/protocols/p-new/analysis": { status: 202, json: { analysis_request_accepted: true } },
      "/api/protocols": { json: { protocols: [] } },
    });
    renderWithClient(<AnalysisScreen protocolId="p-new" />);
    const box = await screen.findByTestId("analysis-failed");
    expect(box).toHaveTextContent("분석하지 못했어요");
    expect(box).toHaveTextContent("분석이 너무 오래 걸려서 멈췄어요. 다시 분석해 주세요.");
    await user.click(screen.getByTestId("analysis-retry"));
    await waitFor(() => expect(calls.some((call) => call.path === "/api/protocols/p-new/analysis" && call.init?.method === "POST")).toBe(true));
  });

  it("a protocol that is not there is said so", async () => {
    fakeFetch({ "/api/protocols/p-x": { status: 404, json: { detail: "protocol_catalog_not_found" } } });
    renderWithClient(<AnalysisScreen protocolId="p-x" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("이 프로토콜을 찾지 못했어요.");
  });
});
