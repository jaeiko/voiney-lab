// Today's values on the screen (lane RX1, decision 3). The server lists the
// values a run may change with the family of units each may be written in
// (start_check.changeable_values: `units_ko` is "초·분·시간", "µL·mL·L" …);
// the screen offers that family as buttons, builds the "20분" / "1.5 mL"
// words the server parses (start_check.parse_run_value) and shows the
// comparison "원문: 15분 → 오늘: 20분". Whether a value is large (below half,
// above twice the source's) is the server's to say: its 422 carries the
// question the screen asks once more.
import type { ChangeableValue, RunChange } from "../api/types";

export interface UnitChoice {
  /** The word a person reads ("분"). */
  label: string;
  /** The spelling sent to the server ("min" → "20분" is also read; the canonical form is sent). */
  unit: string;
}

const KO_UNIT: Record<string, string> = { 초: "s", 분: "min", 시간: "h", 일: "day" };

/** The family's units as buttons, from the server's "초·분·시간" line. */
export function unitChoices(unitsKo: string | null | undefined, sourceUnit: string): UnitChoice[] {
  const parts = String(unitsKo ?? "")
    .split("·")
    .map((part) => part.trim())
    .filter(Boolean);
  const choices = parts.map((label) => ({ label, unit: KO_UNIT[label] ?? label }));
  if (!choices.some((choice) => choice.unit === sourceUnit)) choices.unshift({ label: unitLabel(sourceUnit), unit: sourceUnit });
  return choices;
}

export function unitLabel(unit: string): string {
  return ({ s: "초", min: "분", h: "시간", day: "일" } as Record<string, string>)[unit] ?? unit;
}

/** The words the server reads as a value: "20분", "40°C", "5%", "1.5 mL". */
export function valueText(number: string, unit: string): string {
  const trimmed = number.trim();
  if (unit === "s" || unit === "min" || unit === "h" || unit === "day") return `${trimmed}${unitLabel(unit)}`;
  if (unit === "°C" || unit === "°F" || unit === "%") return `${trimmed}${unit}`;
  return `${trimmed} ${unit}`;
}

export function numberLooksRight(number: string): boolean {
  return /^\d+(?:\.\d+)?$/.test(number.trim());
}

/** The sentence cut around its changeable values, in order, for the marks. */
export interface Segment {
  text: string;
  value: ChangeableValue | null;
}

export function sentenceSegments(text: string, values: ChangeableValue[]): Segment[] {
  const placed = values
    .filter((value) => Number.isInteger(value.start) && Number.isInteger(value.end) && (value.start as number) >= 0 && (value.end as number) > (value.start as number) && (value.end as number) <= text.length)
    .sort((a, b) => (a.start as number) - (b.start as number));
  const segments: Segment[] = [];
  let cursor = 0;
  for (const value of placed) {
    const start = value.start as number;
    const end = value.end as number;
    if (start < cursor) continue;
    if (start > cursor) segments.push({ text: text.slice(cursor, start), value: null });
    segments.push({ text: text.slice(start, end), value });
    cursor = end;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), value: null });
  return segments;
}

/** A chosen value as the list and the comparison say it. */
export interface ChosenValue {
  item_id: string;
  value: ChangeableValue;
  /** The words sent to the server ("20분"). */
  text: string;
  /** The words shown ("20분"). */
  shown: string;
}

export function chosenFromLastTime(values: ChangeableValue[], last: RunChange[]): ChosenValue[] {
  const byId = new Map(values.map((value) => [value.item_id, value]));
  const out: ChosenValue[] = [];
  for (const change of last) {
    const value = byId.get(change.item_id);
    if (!value || out.some((item) => item.item_id === change.item_id)) continue;
    const text = valueText(change.number, change.unit);
    out.push({ item_id: change.item_id, value, text, shown: change.shown_ko ?? text });
  }
  return out;
}
