import { describe, expect, it } from "vitest";

import type { CatalogEntry } from "../api/types";
import { elapsedWords, entryAnalysing, protocolStatus, sortEntries, uploadedDay } from "./protocolStatus";

const base: CatalogEntry = {
  protocol_id: "p",
  title: "T",
  source_filename: "t.pdf",
  available_for_execution: false,
  analysis_status: "structured_analysis_ready",
  lifecycle_state: "ready",
};
const NOW = Date.parse("2026-10-24T10:10:00Z");

describe("protocolStatus", () => {
  it("a runnable protocol: 바로 쓸 수 있어요 and 시작하기", () => {
    const status = protocolStatus({ ...base, available_for_execution: true }, null, NOW);
    expect(status.kind).toBe("ready");
    expect(status.action).toBe("start");
    expect(status.chips.map((chip) => chip.text)).toEqual(["바로 쓸 수 있어요"]);
  });

  it("a review-pending analysis: 확인 N개 남음 and 확인하기", () => {
    const status = protocolStatus(
      { ...base, analysis_status: "review_pending", lifecycle_state: "review_pending", review_pending: { needs_review_count: 8 } },
      null,
      NOW,
    );
    expect(status.kind).toBe("review");
    expect(status.action).toBe("review");
    expect(status.chips.map((chip) => chip.text)).toEqual(["확인 8개 남음"]);
  });

  it("an analysis under way: 분석 중 with the elapsed time and pieces, no button", () => {
    const status = protocolStatus(
      { ...base, analysis_status: "chunk_analysis_in_progress", lifecycle_state: "analyzing" },
      { state: "chunk_analysis_in_progress", requested_at: "2026-10-24T10:07:00Z", total_chunks: 6, completed_chunks: 2 },
      NOW,
    );
    expect(status.kind).toBe("analysing");
    expect(status.action).toBeNull();
    expect(status.chips[0].text).toBe("분석 중 · 3분 지남 · 나눠 읽는 중 2/6");
  });

  it("an analysis under way without a run read yet: 분석 중 alone", () => {
    const status = protocolStatus({ ...base, analysis_status: "analyzing", lifecycle_state: "analyzing" }, null, NOW);
    expect(status.chips[0].text).toBe("분석 중");
    expect(entryAnalysing({ ...base, analysis_status: "analyzing" })).toBe(true);
    expect(entryAnalysing({ ...base, analysis_status: "ocr_in_progress" })).toBe(true);
    expect(entryAnalysing(base)).toBe(false);
  });

  it("a failed analysis: 분석 실패 — 다시 시도 and 다시 시도하기", () => {
    const status = protocolStatus({ ...base, analysis_status: "analysis_failed", lifecycle_state: "analysis_failed" }, null, NOW);
    expect(status.kind).toBe("failed");
    expect(status.action).toBe("retry");
    expect(status.chips[0].text).toBe("분석 실패 — 다시 시도");
  });

  it("OCR states say 사진 글자, never OCR", () => {
    expect(protocolStatus({ ...base, analysis_status: "ocr_in_progress" }, null, NOW).chips[0].text).toBe("사진 글자 읽는 중");
    expect(protocolStatus({ ...base, analysis_status: "ocr_required" }, null, NOW).chips[0].text).toBe("사진 글자 확인 필요");
    expect(protocolStatus({ ...base, analysis_status: "ocr_failed" }, null, NOW).chips[0].text).toBe("사진 글자를 못 읽었어요 — 다시 시도");
  });

  it("a reanalysis of a runnable protocol keeps 바로 쓸 수 있어요 and adds its own chip", () => {
    const status = protocolStatus(
      { ...base, available_for_execution: true },
      { state: "analyzing", reanalysis: true, requested_at: "2026-10-24T10:09:30Z" },
      NOW,
    );
    expect(status.action).toBe("start");
    expect(status.chips.map((chip) => chip.text)).toEqual(["바로 쓸 수 있어요", "다시 분석 중 · 방금 시작했어요"]);
  });

  it("server sentences (analyse-again advice, review version) are shown as they are", () => {
    const status = protocolStatus(
      { ...base, available_for_execution: true, analysis_outdated_ko: "분석기 개선 전 분석 · 다시 분석 권장", current_review_version: { version_ko: "검토본 v2" } },
      null,
      NOW,
    );
    expect(status.chips.map((chip) => chip.text)).toEqual(["바로 쓸 수 있어요", "분석기 개선 전 분석 · 다시 분석 권장", "검토본 v2"]);
  });

  it("anything else: 아직 시작할 수 없어요 without a button", () => {
    const status = protocolStatus({ ...base, lifecycle_state: "blocked", execution_blocker_codes: ["x"] }, null, NOW);
    expect(status.kind).toBe("blocked");
    expect(status.action).toBeNull();
    expect(status.chips[0].text).toBe("아직 시작할 수 없어요");
  });

  it("sorts runnable first, then newest upload first", () => {
    const sorted = sortEntries([
      { ...base, protocol_id: "old", created_at: "2026-10-01T00:00:00Z" },
      { ...base, protocol_id: "ready-old", available_for_execution: true, created_at: "2026-09-01T00:00:00Z" },
      { ...base, protocol_id: "new", created_at: "2026-10-20T00:00:00Z" },
      { ...base, protocol_id: "ready-new", available_for_execution: true, created_at: "2026-10-10T00:00:00Z" },
    ]);
    expect(sorted.map((entry) => entry.protocol_id)).toEqual(["ready-new", "ready-old", "new", "old"]);
  });

  it("elapsed words and the upload day", () => {
    expect(elapsedWords("2026-10-24T10:09:40Z", NOW)).toBe("방금 시작했어요");
    expect(elapsedWords("2026-10-24T09:10:00Z", NOW)).toBe("60분 지남");
    expect(elapsedWords("2026-10-24T07:00:00Z", NOW)).toBe("3시간 지남");
    expect(elapsedWords("not a time", NOW)).toBeNull();
    expect(elapsedWords(null, NOW)).toBeNull();
    expect(uploadedDay("2026-10-19T01:00:00+00:00")).toMatch(/^10월 19일$/);
    expect(uploadedDay(null)).toBeNull();
  });
});
