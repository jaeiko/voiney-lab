// Times as the researcher reads them (lane UI1, person's request of 2026-10-31):
// in the lab's time zone -- the server's VOINEY_LAB_REPORT_TIMEZONE, default
// Asia/Seoul, sent with the records list -- and in the notebook's own form, so
// the screen, the Word file and the PDF say one time the same way:
// "2026년 10월 31일 (토) 13:40", and "13:40" for a moment inside one record.

export const DEFAULT_TIME_ZONE = "Asia/Seoul";

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/** A zone name the browser knows, else the default (a stray value never breaks a list). */
export function knownZone(zone: string | null | undefined): string {
  if (typeof zone !== "string" || !zone.trim()) return DEFAULT_TIME_ZONE;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone.trim() });
    return zone.trim();
  } catch {
    return DEFAULT_TIME_ZONE;
  }
}

interface Parts { year: number; month: number; day: number; weekday: number; hour: number; minute: number; second: number }

function partsOf(value: string | number | null | undefined, zone: string): Parts | null {
  const at = typeof value === "number" ? value : Date.parse(value ?? "");
  if (!Number.isFinite(at)) return null;
  const format = new Intl.DateTimeFormat("en-US", {
    timeZone: knownZone(zone), hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric",
    hour: "numeric", minute: "numeric", second: "numeric", weekday: "short",
  });
  const got: Record<string, string> = {};
  for (const part of format.formatToParts(new Date(at))) got[part.type] = part.value;
  const weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(got.weekday ?? "");
  return {
    year: Number(got.year), month: Number(got.month), day: Number(got.day), weekday,
    hour: Number(got.hour) % 24, minute: Number(got.minute), second: Number(got.second),
  };
}

const two = (value: number) => String(value).padStart(2, "0");

/** "2026년 10월 31일 (토) 13:40" in the zone, or null for no time. */
export function momentWords(value: string | number | null | undefined, zone: string): string | null {
  const parts = partsOf(value, zone);
  if (!parts) return null;
  const weekday = WEEKDAYS[parts.weekday] ?? "";
  return `${parts.year}년 ${parts.month}월 ${parts.day}일${weekday ? ` (${weekday})` : ""} ${two(parts.hour)}:${two(parts.minute)}`;
}

/** "13:40" in the zone (with seconds when asked), or null. */
export function clockWords(value: string | number | null | undefined, zone: string, seconds = false): string | null {
  const parts = partsOf(value, zone);
  if (!parts) return null;
  return `${two(parts.hour)}:${two(parts.minute)}${seconds ? `:${two(parts.second)}` : ""}`;
}

/** "2026-10-31", the day in the zone (what a date filter compares), or "". */
export function dayOf(value: string | null | undefined, zone: string): string {
  const parts = partsOf(value, zone);
  return parts ? `${parts.year}-${two(parts.month)}-${two(parts.day)}` : "";
}
