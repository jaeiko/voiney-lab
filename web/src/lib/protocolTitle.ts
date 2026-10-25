// A protocol's name as a person reads it (lane RX1, decision 4). The
// analysis reads the title from the document and sometimes gets a cover
// line ("표지 최종 하리"), a page number or a running head. When the title
// looks like that, the file's own name stands in for it, and the person can
// give the protocol a name of their own (POST …/title). Nothing here changes
// what the server stores; it only chooses what to show.
import type { CatalogEntry } from "../api/types";
import { title as words } from "../copy/ko";

const PAGE_MARK = /^(?:page|p\.?|pp\.?|쪽|페이지)\s*\d+|^\d+\s*(?:쪽|페이지|page)\b|^\d+(?:\s*[-–/]\s*\d+)?$/i;
const COVER_WORDS = /표지|머리말|머리글|바닥글|running head|supplementary|supplement(?:al|ary) (?:material|information|data)|최종본?|\bfinal\b|\bdraft\b|초안|untitled|제목 없음|\.(?:pdf|docx?|jpe?g|png|heic)$/i;
const ONLY_PUNCTUATION = /^[\s\d\p{P}\p{S}]*$/u;

/** True when the title the analysis read looks like a cover line, a page mark or a running head rather than a name. */
export function titleLooksOdd(title: string | null | undefined): boolean {
  const text = (title ?? "").trim();
  if (!text) return true;
  const letters = text.replace(/[\s\d\p{P}\p{S}]/gu, "");
  if (letters.length < 3) return true;
  if (ONLY_PUNCTUATION.test(text)) return true;
  if (PAGE_MARK.test(text)) return true;
  if (COVER_WORDS.test(text)) return true;
  return false;
}

/** The file's name without its folder and extension, or null when there is none. */
export function fileStem(filename: string | null | undefined): string | null {
  const name = (filename ?? "").split(/[\\/]/).pop()?.trim() ?? "";
  if (!name) return null;
  const stem = name.replace(/\.[A-Za-z0-9]{1,5}$/, "").trim();
  return stem || name;
}

export interface ShownTitle {
  /** What the screen shows as the protocol's name. */
  text: string;
  /** True when the file's name stands in for an odd read title. */
  fromFile: boolean;
  /** The title the analysis read, when it was set aside. */
  readTitle: string | null;
}

export function shownTitle(entry: Pick<CatalogEntry, "title" | "source_filename">): ShownTitle {
  const read = (entry.title ?? "").trim();
  if (!titleLooksOdd(read)) return { text: read, fromFile: false, readTitle: null };
  const stem = fileStem(entry.source_filename);
  if (stem) return { text: stem, fromFile: true, readTitle: read || null };
  return { text: read || words.unnamed, fromFile: false, readTitle: null };
}

export function displayTitle(entry: Pick<CatalogEntry, "title" | "source_filename">): string {
  return shownTitle(entry).text;
}
