import { useState } from "react";

import type { LineSpan, SourceLine } from "../api/types";
import { review as words } from "../copy/ko";

export interface Band {
  first: number;
  last: number;
  kind: "card" | "picked";
}

interface Props {
  src: string;
  page: number;
  lines: SourceLine[];
  /** The image's resolution (dpi) the boxes are placed by: px = pt × dpi / 72. */
  imageDpi: number;
  bands: Band[];
}

interface Placed {
  top: number;
  height: number;
  left: number;
  width: number;
  estimated: boolean;
}

/**
 * The band's place on the image as percentages of the image, from the
 * lines' boxes (points) and the image's resolution; without boxes, the
 * place is guessed from the line's number over the page's line count and
 * said to be a guess (docs/UI_SPEC.md 4.6).
 */
export function placeBand(lines: SourceLine[], band: Pick<Band, "first" | "last">, natural: { width: number; height: number }, imageDpi: number): Placed | null {
  if (!natural.width || !natural.height || !lines.length) return null;
  const inBand = lines.filter((line) => line.number >= band.first && line.number <= band.last);
  const boxes = inBand.map((line) => line.box).filter((box): box is number[] => Array.isArray(box) && box.length === 4);
  if (boxes.length) {
    const scale = imageDpi / 72;
    const x0 = Math.min(...boxes.map((box) => box[0])) * scale;
    const y0 = Math.min(...boxes.map((box) => box[1])) * scale;
    const x1 = Math.max(...boxes.map((box) => box[2])) * scale;
    const y1 = Math.max(...boxes.map((box) => box[3])) * scale;
    return {
      left: Math.max(0, (x0 / natural.width) * 100),
      top: Math.max(0, (y0 / natural.height) * 100),
      width: Math.min(100, ((x1 - x0) / natural.width) * 100),
      height: Math.min(100, ((y1 - y0) / natural.height) * 100),
      estimated: false,
    };
  }
  const count = lines.length;
  const first = Math.max(1, band.first);
  const last = Math.max(first, Math.min(count, band.last));
  return { left: 0, top: ((first - 1) / count) * 100, width: 100, height: ((last - first + 1) / count) * 100, estimated: true };
}

export function spanToBand(span: LineSpan | null, kind: Band["kind"]): Band | null {
  return span ? { first: span.first_line, last: span.last_line, kind } : null;
}

/** The source page with highlight bands where the card's lines stand (lane RX1, decision 2). */
export function PageImage({ src, page, lines, imageDpi, bands }: Props) {
  const [natural, setNatural] = useState<{ width: number; height: number }>({ width: 0, height: 0 });
  const [failed, setFailed] = useState(false);
  const placed = bands.map((band) => ({ band, place: placeBand(lines, band, natural, imageDpi) })).filter((item) => item.place);
  const estimated = placed.some((item) => item.place?.estimated);
  return (
    <figure className="page-image" data-page={page} data-estimated={estimated ? "true" : undefined}>
      <div className="page-image-frame">
        <img
          src={src}
          alt={words.sourcePage(page)}
          onLoad={(event) => setNatural({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
          onError={() => setFailed(true)}
          draggable={false}
        />
        {placed.map(({ band, place }, index) => (
          <span
            key={`${band.kind}-${band.first}-${band.last}-${index}`}
            className="page-band"
            data-kind={band.kind}
            aria-hidden="true"
            style={{ top: `${place!.top}%`, left: `${place!.left}%`, width: `${place!.width}%`, height: `${place!.height}%` }}
          />
        ))}
      </div>
      <figcaption className="muted">
        {failed ? words.pageFailed : words.sourcePage(page)}
        {estimated && !failed ? ` · ${words.estimated}` : ""}
      </figcaption>
    </figure>
  );
}
