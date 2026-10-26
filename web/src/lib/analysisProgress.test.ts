import { describe, expect, it } from "vitest";

import type { ProtocolDetail } from "../api/types";
import { elapsedClock, estimatedMinutesLeft, estimateWords, failureWords, pieceLines, runPhase } from "./analysisProgress";

const NOW = Date.parse("2026-10-25T10:10:00Z");
const base: ProtocolDetail = {
  protocol_id: "p",
  title: "T",
  source_filename: "t.pdf",
  available_for_execution: false,
  analysis_status: "analyzing",
  lifecycle_state: "analyzing",
  analysis_run: { state: "analyzing", requested_at: "2026-10-25T10:07:30Z", total_chunks: 1, completed_chunks: 0 },
};

describe("analysisProgress", () => {
  it("the elapsed clock is mm:ss from the server's request time", () => {
    expect(elapsedClock("2026-10-25T10:07:30Z", NOW)).toBe("02:30");
    expect(elapsedClock("2026-10-25T09:00:00Z", NOW)).toBe("70:00");
    expect(elapsedClock(null, NOW)).toBeNull();
    expect(elapsedClock("2026-10-25T11:00:00Z", NOW)).toBeNull();
  });

  it("pieces say their pages and state", () => {
    const lines = pieceLines([
      { chunk_id: "piece-1", source_page_start: 1, source_page_end: 15, status: "completed" },
      { chunk_id: "piece-2", source_page_start: 16, source_page_end: 30, status: "in_progress" },
      { chunk_id: "piece-3", source_page_start: 31, source_page_end: 31, status: "pending" },
    ]);
    expect(lines.map((line) => line.text)).toEqual(["1–15쪽 · 끝", "16–30쪽 · 읽는 중", "31쪽 · 기다리는 중"]);
    expect(pieceLines(undefined)).toEqual([]);
  });

  it("estimates the time left only from pieces already done, at the pace so far", () => {
    // 2 of 6 pieces in 3 minutes: 1.5 min per piece, 4 left -> 6 minutes.
    const run = { state: "chunk_analysis_in_progress", requested_at: "2026-10-25T10:07:00Z", total_chunks: 6, completed_chunks: 2 };
    expect(estimatedMinutesLeft(run, NOW)).toBe(6);
    expect(estimateWords(6)).toBe("이대로면 약 6분 더 걸려요 (지금까지 속도로 어림)");
    expect(estimateWords(0)).toBe("곧 끝나요 (지금까지 속도로 어림)");
    // Nothing done yet, one piece only, or no request time: no number is made up.
    expect(estimatedMinutesLeft({ ...run, completed_chunks: 0 }, NOW)).toBeNull();
    expect(estimatedMinutesLeft({ ...run, total_chunks: 1, completed_chunks: 0 }, NOW)).toBeNull();
    expect(estimatedMinutesLeft({ ...run, requested_at: null }, NOW)).toBeNull();
    expect(estimateWords(null)).toBeNull();
  });

  it("reads the phase from the server's fields alone", () => {
    expect(runPhase(base)).toBe("running");
    expect(runPhase({ ...base, available_for_execution: true, analysis_status: "structured_analysis_ready", lifecycle_state: "ready", analysis_run: { state: "complete" } })).toBe("ready");
    expect(runPhase({ ...base, analysis_status: "review_pending", lifecycle_state: "review_pending", review_pending: { needs_review_count: 3 }, analysis_run: { state: "review_pending" } })).toBe("review");
    expect(runPhase({ ...base, analysis_status: "analysis_failed", lifecycle_state: "analysis_failed", analysis_run: { state: "failed", failure_code: "provider_timeout" } })).toBe("failed");
    expect(runPhase({ ...base, analysis_status: "ocr_in_progress", lifecycle_state: "analyzing", analysis_run: { state: "uploaded" } })).toBe("ocr");
    expect(runPhase({ ...base, analysis_status: "structured_analysis_ready", lifecycle_state: "uploaded", analysis_run: { state: "uploaded" } })).toBe("idle");
    // A runnable protocol being analysed again stays runnable: the progress is the row's, not this screen's.
    expect(runPhase({ ...base, available_for_execution: true, analysis_run: { state: "analyzing", reanalysis: true } })).toBe("running");
    expect(runPhase(null)).toBe("idle");
  });

  it("says a failure in plain words, by its code or its family", () => {
    expect(failureWords("provider_configuration_missing")).toContain("설정되지 않았어요");
    expect(failureWords("chunk_timeout")).toContain("긴 문서");
    expect(failureWords("something_timeout_else")).toContain("오래 걸려서");
    expect(failureWords("evidence_verification_failed")).toContain("원문과 맞지");
    expect(failureWords(null)).toBe("다시 분석해 주세요.");
  });
});
