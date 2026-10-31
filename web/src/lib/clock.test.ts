import { describe, expect, it } from "vitest";

import { clockWords, dayOf, DEFAULT_TIME_ZONE, knownZone, momentWords } from "./clock";

describe("times in the lab's zone, in the notebook's form (lane UI1)", () => {
  it("says the 10/31 run's start as the notebook does, in Seoul", () => {
    expect(momentWords("2026-10-31T04:40:22.571784+00:00", "Asia/Seoul")).toBe("2026년 10월 31일 (토) 13:40");
    expect(clockWords("2026-10-31T04:40:22+00:00", "Asia/Seoul")).toBe("13:40");
    expect(clockWords("2026-10-31T04:40:22+00:00", "Asia/Seoul", true)).toBe("13:40:22");
  });

  it("moves the day with the zone", () => {
    expect(momentWords("2026-10-28T16:38:00+00:00", "Asia/Seoul")).toBe("2026년 10월 29일 (목) 01:38");
    expect(momentWords("2026-10-28T16:38:00+00:00", "UTC")).toBe("2026년 10월 28일 (수) 16:38");
    expect(dayOf("2026-10-28T16:38:00+00:00", "Asia/Seoul")).toBe("2026-10-29");
    expect(dayOf("2026-10-28T16:38:00+00:00", "Europe/Berlin")).toBe("2026-10-28");
  });

  it("never says UTC unless the lab chose it, and a stray zone falls back to the default", () => {
    expect(momentWords("2026-10-31T00:00:00Z", "Asia/Seoul")).not.toMatch(/UTC/);
    expect(knownZone("Not/AZone")).toBe(DEFAULT_TIME_ZONE);
    expect(knownZone("")).toBe(DEFAULT_TIME_ZONE);
    expect(knownZone(null)).toBe(DEFAULT_TIME_ZONE);
    expect(momentWords("2026-10-31T04:40:22Z", "Not/AZone")).toBe("2026년 10월 31일 (토) 13:40");
  });

  it("says nothing for no time", () => {
    expect(momentWords(null, "Asia/Seoul")).toBeNull();
    expect(momentWords("soon", "Asia/Seoul")).toBeNull();
    expect(clockWords(undefined, "Asia/Seoul")).toBeNull();
    expect(dayOf(null, "Asia/Seoul")).toBe("");
  });
});
