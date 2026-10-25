import { useState } from "react";

import { sourcePageImageUrl } from "../api/queries";
import { upload as words } from "../copy/ko";

interface Props {
  protocolId: string;
  pageCount: number;
  onChoose: (first: number, last: number) => void;
  onReset?: () => void;
  busy?: boolean;
}

export interface Picked {
  first: number | null;
  last: number | null;
}

/** The next pick: the first tap sets the start page, the second the end page (either order; the same page twice is one page). */
export function pickPage(picked: Picked, page: number): Picked {
  if (picked.first === null || picked.last !== null) return { first: page, last: null };
  return { first: Math.min(picked.first, page), last: Math.max(picked.first, page) };
}

/**
 * The upload's page picker (lane RX1, decision 1): a small picture of every
 * page from the server (`…/source-pages/{n}/image?size=thumb`), a start page
 * and an end page by two taps, and one button for those pages only.
 */
export function PagePicker({ protocolId, pageCount, onChoose, onReset, busy = false }: Props) {
  const [picked, setPicked] = useState<Picked>({ first: null, last: null });
  const pages = Array.from({ length: Math.max(0, pageCount) }, (_, index) => index + 1);
  const complete = picked.first !== null && picked.last !== null;
  const hint = picked.first === null ? words.pagesFirst : picked.last === null ? words.pagesLast : words.pagesChosen(picked.first, picked.last);

  function within(page: number): boolean {
    if (picked.first === null) return false;
    const last = picked.last ?? picked.first;
    return page >= picked.first && page <= last;
  }

  return (
    <div className="page-picker" data-testid="page-picker">
      <p className="page-picker-hint" role="status" aria-live="polite" data-testid="page-picker-hint">
        {hint}
      </p>
      <ol className="page-grid" aria-label={words.pagesCount(pageCount)}>
        {pages.map((page) => (
          <li key={page}>
            <button
              type="button"
              className="page-thumb"
              aria-pressed={within(page)}
              data-page={page}
              disabled={busy}
              onClick={() => setPicked((current) => pickPage(current, page))}
            >
              <img src={sourcePageImageUrl(protocolId, page, "thumb")} alt="" loading="lazy" draggable={false} />
              <span>{words.pageLabel(page)}</span>
            </button>
          </li>
        ))}
      </ol>
      <div className="page-picker-actions">
        <button type="button" className="btn btn-primary" disabled={!complete || busy} onClick={() => complete && onChoose(picked.first as number, picked.last as number)} data-testid="pages-start">
          {words.pagesStart}
        </button>
        <button
          type="button"
          className="btn btn-quiet"
          disabled={busy}
          onClick={() => {
            setPicked({ first: null, last: null });
            onReset?.();
          }}
        >
          {words.pagesReset}
        </button>
      </div>
    </div>
  );
}
