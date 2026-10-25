import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SourceLine } from "../api/types";
import { SOURCE_PAGE_1 } from "../test/helpers";
import { PageImage, placeBand } from "./PageImage";

const lines = SOURCE_PAGE_1.lines as SourceLine[];
// A 612 x 792 pt page rendered at 120 dpi.
const natural = { width: 1020, height: 1320 };

describe("placeBand", () => {
  it("puts the band where the lines' boxes are, as percentages of the image, by the image's dpi", () => {
    const place = placeBand(lines, { first: 4, last: 4 }, natural, 120);
    expect(place?.estimated).toBe(false);
    // x0 72 pt -> 120 px of 1020 = 11.76 %; y0 94 pt -> 156.7 px of 1320 = 11.87 %.
    expect(place?.left).toBeCloseTo(11.76, 1);
    expect(place?.top).toBeCloseTo(11.87, 1);
    expect(place?.width).toBeCloseTo(((230 - 72) * (120 / 72)) / 1020 * 100, 1);
    expect(place?.height).toBeCloseTo((12 * (120 / 72)) / 1320 * 100, 1);
    // Two lines: one band over both.
    const two = placeBand(lines, { first: 3, last: 4 }, natural, 120);
    expect(two?.top).toBeCloseTo(((80 * 120) / 72 / 1320) * 100, 1);
    expect(two?.height).toBeCloseTo(((106 - 80) * (120 / 72)) / 1320 * 100, 1);
  });

  it("guesses from the line's number when the lines have no boxes, and says so", () => {
    const bare = lines.map((line) => ({ ...line, box: null }));
    const place = placeBand(bare, { first: 2, last: 3 }, natural, 120);
    expect(place).toEqual({ left: 0, top: 20, width: 100, height: 40, estimated: true });
    expect(placeBand(lines, { first: 1, last: 1 }, { width: 0, height: 0 }, 120)).toBeNull();
  });
});

describe("PageImage", () => {
  it("draws the page with a band once the image's size is known, and names a guessed place", () => {
    const { container, rerender } = render(<PageImage src="/img.png" page={1} lines={lines} imageDpi={120} bands={[{ first: 4, last: 4, kind: "card" }]} />);
    const img = screen.getByRole("img", { name: "원문 1쪽" });
    expect(container.querySelector(".page-band")).toBeNull();
    Object.defineProperty(img, "naturalWidth", { value: 1020, configurable: true });
    Object.defineProperty(img, "naturalHeight", { value: 1320, configurable: true });
    fireEvent.load(img);
    const band = container.querySelector(".page-band") as HTMLElement;
    expect(band).not.toBeNull();
    expect(band.dataset.kind).toBe("card");
    expect(band.style.top).toMatch(/^11\.8/);
    expect(container.textContent).not.toContain("어림 위치");
    rerender(<PageImage src="/img.png" page={1} lines={lines.map((line) => ({ ...line, box: null }))} imageDpi={120} bands={[{ first: 4, last: 4, kind: "card" }]} />);
    expect(container.textContent).toContain("어림 위치");
  });
});
