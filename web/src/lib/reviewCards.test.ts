import { describe, expect, it } from "vitest";

import type { ReviewCard, ReviewDraft } from "../api/types";
import { CARD_CLAIM, CARD_EVIDENCE, REVIEW_DRAFT } from "../test/helpers";
import { cardLines, cardPage, decisionWords, firstCardIndex, needsOf, nextUndecided, pickExtends, undecidedCount } from "./reviewCards";

const evidence = CARD_EVIDENCE as ReviewCard;
const claim = CARD_CLAIM as ReviewCard;

describe("reviewCards", () => {
  it("reads what a button needs from the person", () => {
    expect(needsOf({ needs: "source_lines" })).toEqual({ lines: true, text: false, seconds: false, memo: false });
    expect(needsOf({ needs: "source_lines_and_text" })).toEqual({ lines: true, text: true, seconds: false, memo: false });
    expect(needsOf({ needs: "text" }).text).toBe(true);
    expect(needsOf({ needs: "seconds" }).seconds).toBe(true);
    expect(needsOf({ needs: null }).lines).toBe(false);
  });

  it("finds the card's page and lines: the server's suggestion first, then the verified quote's lines", () => {
    expect(cardPage(evidence)).toBe(1);
    expect(cardLines(evidence)).toEqual({ page: 1, first_line: 4, last_line: 4 });
    expect(cardPage(claim)).toBe(2);
    expect(cardLines(claim)).toEqual({ page: 2, first_line: 3, last_line: 3 });
    expect(cardPage({ ...claim, source: null, page: null })).toBeNull();
  });

  it("the first card is the server's next undecided one, the count and the next card follow decisions", () => {
    const draft = REVIEW_DRAFT as unknown as ReviewDraft;
    expect(firstCardIndex(draft)).toBe(0);
    expect(firstCardIndex({ ...draft, next_card: null, cards: [{ ...evidence, decided: true }, claim] })).toBe(1);
    expect(firstCardIndex({ ...draft, next_card: null, cards: [{ ...evidence, decided: true }, { ...claim, decided: true }] })).toBeNull();
    expect(undecidedCount([evidence, { ...claim, decided: true }])).toBe(1);
    expect(nextUndecided([{ ...evidence, decided: true }, claim], 0)).toBe(1);
    expect(nextUndecided([evidence, { ...claim, decided: true }], 1)).toBe(0);
    expect(nextUndecided([{ ...evidence, decided: true }, { ...claim, decided: true }], 0)).toBeNull();
  });

  it("says a decision back in the button's words with the value written", () => {
    expect(decisionWords({ ...claim, decided: true, decision: { action: "confirm" } })).toBe("맞아요");
    expect(decisionWords({ ...claim, decided: true, decision: { action: "fix", value: { text: "600 x g" } } })).toBe("직접 고칠게요 · 600 x g");
    expect(decisionWords({ ...claim, decided: true, decision: null, closed_ko: "되물어서 닫힘" })).toBe("되물어서 닫힘");
  });

  it("a tap on the line beside the picked ones extends them; any other starts over", () => {
    expect(pickExtends(null, 1, 4, false)).toEqual({ page: 1, first: 4, last: 4 });
    expect(pickExtends({ page: 1, first: 4, last: 4 }, 1, 5, false)).toEqual({ page: 1, first: 4, last: 5 });
    expect(pickExtends({ page: 1, first: 4, last: 5 }, 1, 3, false)).toEqual({ page: 1, first: 3, last: 5 });
    expect(pickExtends({ page: 1, first: 4, last: 5 }, 1, 9, false)).toEqual({ page: 1, first: 9, last: 9 });
    expect(pickExtends({ page: 1, first: 4, last: 5 }, 1, 9, true)).toEqual({ page: 1, first: 4, last: 9 });
    expect(pickExtends({ page: 1, first: 4, last: 5 }, 2, 5, false)).toEqual({ page: 2, first: 5, last: 5 });
  });
});
