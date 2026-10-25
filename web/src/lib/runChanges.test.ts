import { describe, expect, it } from "vitest";

import type { ChangeableValue } from "../api/types";
import { RUN_VALUES } from "../test/helpers";
import { chosenFromLastTime, numberLooksRight, sentenceSegments, unitChoices, valueText } from "./runChanges";

const values = RUN_VALUES as ChangeableValue[];

describe("runChanges", () => {
  it("offers the family's units from the server's line, the source unit included", () => {
    expect(unitChoices("초·분·시간", "min")).toEqual([
      { label: "초", unit: "s" },
      { label: "분", unit: "min" },
      { label: "시간", unit: "h" },
    ]);
    expect(unitChoices("µL·mL·L", "mL").map((choice) => choice.unit)).toEqual(["µL", "mL", "L"]);
    expect(unitChoices("°C", "°C")).toEqual([{ label: "°C", unit: "°C" }]);
    expect(unitChoices("", "rpm")).toEqual([{ label: "rpm", unit: "rpm" }]);
  });

  it("writes a value the server reads: 20분, 40°C, 5%, 1.5 mL", () => {
    expect(valueText("20", "min")).toBe("20분");
    expect(valueText("1.5", "h")).toBe("1.5시간");
    expect(valueText("40", "°C")).toBe("40°C");
    expect(valueText("5", "%")).toBe("5%");
    expect(valueText("1.5", "mL")).toBe("1.5 mL");
    expect(valueText("500", "×g")).toBe("500 ×g");
    expect(numberLooksRight("20")).toBe(true);
    expect(numberLooksRight("1.5")).toBe(true);
    expect(numberLooksRight("abc")).toBe(false);
    expect(numberLooksRight("")).toBe(false);
  });

  it("cuts the step's sentence around its values, in order", () => {
    const segments = sentenceSegments("2. Incubate for 10 min at 37 C.", values.filter((value) => value.step_id === "s2"));
    expect(segments.map((segment) => [segment.text, segment.value?.item_id ?? null])).toEqual([
      ["2. Incubate for ", null],
      ["10 min", "s2#1"],
      [" at ", null],
      ["37 C", "s2#2"],
      [".", null],
    ]);
    expect(sentenceSegments("no values here", [])).toEqual([{ text: "no values here", value: null }]);
    // A value without a place in the sentence is left out of the marks.
    expect(sentenceSegments("x", [{ ...values[0], start: -1, end: -1 }])).toEqual([{ text: "x", value: null }]);
  });

  it("last time's values come back on today's list where the item still exists", () => {
    const chosen = chosenFromLastTime(values, [
      { item_id: "s2#1", step_label: "2", kind: "time", source_number: "10", source_unit: "min", number: "20", unit: "min", shown_ko: "20분" },
      { item_id: "gone#1", step_label: "9", kind: "time", source_number: "1", source_unit: "min", number: "2", unit: "min" },
    ]);
    expect(chosen.map((item) => [item.item_id, item.text, item.shown])).toEqual([["s2#1", "20분", "20분"]]);
  });
});
