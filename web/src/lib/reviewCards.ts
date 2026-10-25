// Reading a review card's fields (lane RX1, decision 2). The server builds
// each card (protocol_catalog.review_cards): the question, the item as read,
// where it stands in the source and the only buttons the screen may draw.
// The screen draws from those fields alone and never from the card's type.
import type { CardChoice, LineSpan, ReviewCard, ReviewDraft } from "../api/types";

export interface Needs {
  lines: boolean;
  text: boolean;
  seconds: boolean;
  memo: boolean;
}

export function needsOf(choice: Pick<CardChoice, "needs">): Needs {
  const needs = String(choice.needs ?? "");
  return {
    lines: needs.includes("source_lines"),
    text: needs === "text" || needs.endsWith("_text"),
    seconds: needs === "seconds",
    memo: needs === "memo",
  };
}

/** The page the card is about: its source page, or the page named by its lines. */
export function cardPage(card: ReviewCard | null | undefined): number | null {
  if (!card) return null;
  const candidates = [card.source?.suggested_lines?.page, card.source?.lines?.page, card.source?.page, card.page];
  for (const page of candidates) if (Number.isInteger(page) && (page as number) > 0) return page as number;
  return null;
}

/** The lines the card points at on its page: the server's suggestion first, then the verified quote's lines. */
export function cardLines(card: ReviewCard | null | undefined): LineSpan | null {
  if (!card?.source) return null;
  const span = card.source.suggested_lines ?? card.source.lines ?? null;
  if (!span || !Number.isInteger(span.first_line)) return null;
  return { page: span.page, first_line: span.first_line, last_line: Number.isInteger(span.last_line) ? span.last_line : span.first_line };
}

export function cardsOf(draft: ReviewDraft | null | undefined): ReviewCard[] {
  return Array.isArray(draft?.cards) ? draft!.cards.filter((card) => card && typeof card.card_id === "string") : [];
}

export function undecidedCount(cards: ReviewCard[]): number {
  return cards.filter((card) => card.decided !== true).length;
}

/** The index to show first: the server's next undecided card, else the first undecided, else none (all done). */
export function firstCardIndex(draft: ReviewDraft | null | undefined): number | null {
  const cards = cardsOf(draft);
  const next = draft?.next_card;
  if (Number.isInteger(next) && (next as number) >= 0 && (next as number) < cards.length) return next as number;
  const index = cards.findIndex((card) => card.decided !== true);
  return index >= 0 ? index : null;
}

/** The next undecided card after `from`, else null. */
export function nextUndecided(cards: ReviewCard[], from: number): number | null {
  for (let index = from + 1; index < cards.length; index += 1) if (cards[index].decided !== true) return index;
  for (let index = 0; index < from; index += 1) if (cards[index].decided !== true) return index;
  return null;
}

/** A decision as the card can say it back: the button's words, the value written, the lines picked. */
export function decisionWords(card: ReviewCard): string | null {
  const decision = card.decision;
  if (!decision) return card.closed_ko ?? null;
  const value = decision.value;
  const text = typeof value?.text === "string" && value.text ? value.text : null;
  const seconds = Number.isInteger(value?.seconds) ? `${Math.round((value!.seconds as number) / 60)}분` : null;
  const action = decision.action ?? "";
  const choice = card.choices.find((item) => item.request?.action === action);
  const label = choice?.label_ko ?? action;
  const detail = text ?? seconds;
  return detail ? `${label} · ${detail}` : label || null;
}

export function pickExtends(picked: { page: number; first: number; last: number } | null, page: number, line: number, shift: boolean): { page: number; first: number; last: number } {
  const same = picked && picked.page === page;
  const extend = Boolean(same && (shift || line === picked!.last + 1 || line === picked!.first - 1));
  return extend ? { page, first: Math.min(picked!.first, line), last: Math.max(picked!.last, line) } : { page, first: line, last: line };
}
