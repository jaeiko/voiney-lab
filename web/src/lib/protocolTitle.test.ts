import { describe, expect, it } from "vitest";

import { displayTitle, fileStem, shownTitle, titleLooksOdd } from "./protocolTitle";

describe("protocolTitle", () => {
  it("a cover line, a page mark, a running head or a very short title looks odd", () => {
    for (const odd of ["표지 최종 하리", "Page 3", "p. 12", "3", "12쪽", "Running head: Methods", "Supplementary Material", "ab", "", "   ", "---", "western.pdf", "Untitled"]) {
      expect(titleLooksOdd(odd), odd).toBe(true);
    }
  });

  it("a real title does not", () => {
    for (const fine of ["In-gel digestion", "Western blot protocol", "단백질 정량 (Bradford)", "RNA extraction from leaves", "CRISPR knock-in"]) {
      expect(titleLooksOdd(fine), fine).toBe(false);
    }
  });

  it("the file's name stands in for an odd title and the read title is kept beside it", () => {
    const shown = shownTitle({ title: "표지 최종 하리", source_filename: "western-blot.pdf" });
    expect(shown).toEqual({ text: "western-blot", fromFile: true, readTitle: "표지 최종 하리" });
    expect(displayTitle({ title: "In-gel digestion", source_filename: "in-gel.pdf" })).toBe("In-gel digestion");
    expect(displayTitle({ title: "", source_filename: null })).toBe("이름 없는 프로토콜");
    expect(fileStem("folder/western blot.v2.docx")).toBe("western blot.v2");
    expect(fileStem(null)).toBeNull();
  });
});
