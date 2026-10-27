import { screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ENTRY_ANALYSING, ENTRY_FAILED, ENTRY_READY, ENTRY_REVIEW, fakeFetch, renderWithClient } from "../test/helpers";
import { ProtocolsScreen } from "./ProtocolsScreen";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ProtocolsScreen", () => {
  it("loading state", () => {
    fakeFetch({ "/api/protocols": () => new Promise(() => {}) as never });
    renderWithClient(<ProtocolsScreen />);
    expect(screen.getByRole("status")).toHaveTextContent("불러오는 중…");
  });

  it("empty state: the sentence and 첫 프로토콜 올리기", async () => {
    fakeFetch({ "/api/protocols": { json: { protocols: [] } } });
    renderWithClient(<ProtocolsScreen />);
    expect(await screen.findByText("아직 올린 프로토콜이 없어요.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "첫 프로토콜 올리기" })).toHaveAttribute("href", "#/upload");
  });

  it("error state: the sentence and 다시 불러오기", async () => {
    fakeFetch({ "/api/protocols": new TypeError("Failed to fetch") });
    renderWithClient(<ProtocolsScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("프로토콜 목록을 불러오지 못했어요.");
    expect(screen.getByRole("button", { name: "다시 불러오기" })).toBeInTheDocument();
  });

  it("normal state: a chip and the next action per row, runnable first", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-10-24T01:03:00Z"), toFake: ["Date"] });
    fakeFetch({
      "/api/protocols": { json: { protocols: [ENTRY_FAILED, ENTRY_REVIEW, ENTRY_ANALYSING, ENTRY_READY] } },
      "/api/protocols/p-analysing": { json: { ...ENTRY_ANALYSING, analysis_run: { state: "analyzing", requested_at: "2026-10-24T01:00:00Z", total_chunks: 1 } } },
    });
    renderWithClient(<ProtocolsScreen />);
    const rows = await screen.findAllByRole("listitem");
    expect(rows.map((row) => row.getAttribute("data-protocol-id"))).toEqual(["p-ready", "p-analysing", "p-failed", "p-review"]);

    const ready = within(rows[0]);
    expect(ready.getByText("바로 쓸 수 있어요")).toBeInTheDocument();
    // Lane RX2: the start goes to the new screen's start flow (startHref).
    expect(ready.getByRole("link", { name: "시작하기" })).toHaveAttribute("href", "#/start/p-ready");
    expect(ready.getByText("in-gel-digestion.pdf · 9단계 · 10월 19일 올림")).toBeInTheDocument();

    const analysing = within(rows[1]);
    await vi.waitFor(() => expect(analysing.getByText("분석 중 · 3분 지남")).toBeInTheDocument());
    // A row being analysed opens its progress screen (lane RX1); it has no start or review button.
    expect(analysing.getByRole("link", { name: "진행 보기" })).toHaveAttribute("href", "#/protocols/p-analysing/analysis");

    const failed = within(rows[2]);
    expect(failed.getByText("분석 실패 — 다시 시도")).toBeInTheDocument();
    expect(failed.getByRole("link", { name: "다시 시도하기" })).toHaveAttribute("href", "#/protocols/p-failed/analysis");

    const review = within(rows[3]);
    expect(review.getByText("확인 2개 남음")).toBeInTheDocument();
    expect(review.getByRole("link", { name: "확인하기" })).toHaveAttribute("href", "#/protocols/p-review/review");

    expect(screen.getByTestId("protocols-count")).toHaveTextContent("4개 · 바로 쓸 수 있는 것 1개");
    expect(screen.getByTestId("protocols-upload")).toHaveAttribute("href", "#/upload");
    // No internal word anywhere on the screen.
    for (const word of ["검토 대기", "실행 가능", "실행 불가", "카탈로그", "OCR"]) expect(screen.queryByText(new RegExp(word))).toBeNull();
    vi.useRealTimers();
  });
});

it('keeps full protocol details reachable from a ready protocol title', async () => {
  fakeFetch({ '/api/protocols': { json: { protocols: [ENTRY_READY] } } });
  renderWithClient(<ProtocolsScreen />);
  expect(await screen.findByRole('link', { name: 'In-gel digestion' })).toHaveAttribute('href', '#/protocols/p-ready');
});
