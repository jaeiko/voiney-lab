import { describe, expect, it } from "vitest";

import { captionPages, pageMs, sentencesOf } from "./CaptionBar";

describe("caption pages", () => {
  it("splits at sentence stops", () => {
    expect(sentencesOf("하나예요. 둘이에요! 셋인가요? 넷")).toEqual(["하나예요.", "둘이에요!", "셋인가요?", "넷"]);
  });

  it("keeps whole sentences on a page while they fit, and never cuts one unless it alone is too long", () => {
    const fits = (text: string) => text.length <= 20;
    expect(captionPages("짧은 말이에요. 또 짧아요.", fits)).toEqual(["짧은 말이에요. 또 짧아요."]);
    expect(captionPages("첫 문장은 조금 길어요. 둘째 문장도 길어요.", fits)).toEqual(["첫 문장은 조금 길어요.", "둘째 문장도 길어요."]);
    const long = captionPages("아주 아주 아주 아주 아주 아주 긴 한 문장이에요.", fits);
    expect(long.every((page) => page.length <= 20)).toBe(true);
    expect(long.join(" ")).toBe("아주 아주 아주 아주 아주 아주 긴 한 문장이에요.");
  });

  it("shows a page long enough to read, not for ever", () => {
    expect(pageMs("짧아요.")).toBe(2500);
    expect(pageMs("가".repeat(200))).toBe(7000);
  });
});
