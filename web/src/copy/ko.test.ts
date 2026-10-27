import { describe, expect, it } from "vitest";

import * as copy from "./ko";

// Every sentence the screen shows follows docs/VOICE_AND_TONE.md: 해요체,
// no internal words (docs/COPY_DECK.md), buttons as ~기 verbs.
const INTERNAL_WORDS = [
  "검토본", "리비전", "개정", "사유 코드", "개발 상세 정보", "LAB COPILOT", "Lab Copilot", "카탈로그", "세션",
  "체크포인트", "워크플로", "타임라인", "서버", "모델", "공급자", "OCR", "구조화", "실행 가능", "실행 불가", "검토 대기",
  "승인", "환경 변수", "VOINEY_LAB_",
];
const FORMAL_ENDINGS = /(습니다|십시오|됩니다|입니다|합니다)[.!?]?$/;

function sentences(value: unknown, path: string, out: Array<[string, string]>): void {
  if (typeof value === "string") out.push([path, value]);
  else if (typeof value === "function") {
    // Called with sample values so the template's own words are checked.
    const sample = (value as (...args: unknown[]) => string)("예시", 3);
    out.push([path, String(sample)]);
  } else if (value && typeof value === "object") {
    for (const [key, inner] of Object.entries(value)) sentences(inner, `${path}.${key}`, out);
  }
}

function allSentences(): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  sentences(copy, "copy", out);
  return out;
}

describe("the copy table", () => {
  it("uses no internal word on the screen", () => {
    const offenders = allSentences().filter(([, text]) => INTERNAL_WORDS.some((word) => text.includes(word)));
    expect(offenders).toEqual([]);
  });

  it("speaks 해요체, never 합니다체", () => {
    const offenders = allSentences().filter(([, text]) => FORMAL_ENDINGS.test(text.trim()));
    expect(offenders).toEqual([]);
  });

  it("names buttons as verbs ending in 기", () => {
    const buttons = [
      copy.auth.loginButton,
      copy.auth.signupButton,
      copy.auth.retry,
      copy.frame.logout,
      copy.frame.logoutConfirm,
      copy.frame.cancel,
      copy.states.reload,
      copy.home.first,
      copy.home.review,
      copy.home.resume,
      copy.protocols.upload,
      copy.protocols.emptyAction,
      ...Object.values(copy.protocols.actions),
      ...Object.values(copy.home.secondary),
      copy.upload.pick,
      copy.upload.linkButton,
      copy.upload.pagesPick,
      copy.upload.pagesStart,
      copy.upload.pagesReset,
      copy.upload.pickAgain,
      copy.analysis.review,
      copy.analysis.retry,
      copy.analysis.startAnalysis,
      copy.analysis.uploadOther,
      copy.review.later,
      copy.review.openSource,
      copy.review.send,
      copy.review.cancel,
      copy.review.memo,
      copy.changes.remove,
      copy.changes.asText,
      copy.changes.asNumber,
      copy.title.rename,
      copy.title.save,
      copy.records.word, copy.records.pdf, copy.records.md, copy.records.json, copy.records.csv,
      copy.records.meeting, copy.records.meetingPdf, copy.records.xlsx, copy.records.refresh,
      copy.records.confirm, copy.records.edit, copy.records.correct, copy.records.photoGet, copy.records.piSave,
    ];
    // The person's own button words (brief 2026-10-25) and the short way-finders stand as they are.
    const asWritten = ["프로토콜 보기", "지난 기록", "실험 시작", "전체 분석", "목록으로", "이전", "다음"];
    const offenders = buttons.filter((label) => !/기$/.test(label) && !asWritten.includes(label));
    expect(offenders).toEqual([]);
    expect(copy.changes.none).toBe("바꿀 값 없음 — 원문대로 할게요");
    expect(copy.changes.some).toBe("바꿀 값이 있어요");
    expect(copy.changes.done).toBe("다 골랐어요");
    expect(copy.upload.pagesAsk).toBe("어느 쪽을 분석할까요?");
  });

  it("keeps the product name and the tagline the brief asked for", () => {
    expect(copy.product.name).toBe("Voiney Lab");
    expect(copy.product.tagline).toContain("장갑");
  });

  it("tells a locked login how long to wait from the server's number", () => {
    expect(copy.auth.errors.login_locked(3)).toBe("여러 번 틀려 잠시 막혔어요. 3분 뒤에 다시 해 주세요.");
  });
});
