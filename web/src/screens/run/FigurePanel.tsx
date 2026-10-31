import * as Dialog from "@radix-ui/react-dialog";
import { ExternalLink, ImageOff, Maximize2, X } from "lucide-react";
import { useState } from "react";

import { run as copy } from "../../copy/ko";
import type { Figure } from "../../voice/runModel";

// The step's pictures, large (person's decision 2): the source's figures, a
// picture the AI drew from the source's words when asked, a free-licence web
// photo the web lookup found (lane UI1), each named by where it comes from (a
// web photo with its credit and its page). A press opens it across the whole
// screen. With none, one line says so (lane UI1: the panel keeps its place).

const ORIGIN_WORD = { source: copy.figures.source, drawn: copy.figures.drawn, web: copy.figures.web } as const;

function FigureImage({ figure, big }: { figure: Figure; big: boolean }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <p className="muted">{copy.figures.failed}</p>;
  return <img src={figure.url} alt={figure.caption || figure.label} className={big ? "run-figure-full" : "run-figure-image"} onError={() => setFailed(true)} draggable={false} />;
}

function Credit({ figure }: { figure: Figure }) {
  if (figure.origin !== "web") return null;
  return (
    <span className="run-figure-credit" data-testid="figure-credit">
      <span>{[figure.credit, copy.figures.notSource].filter(Boolean).join(" · ")}</span>
      {figure.link ? (
        <a href={figure.link} target="_blank" rel="noopener noreferrer">
          <ExternalLink aria-hidden="true" />
          <span>{copy.figures.webPage}</span>
        </a>
      ) : null}
    </span>
  );
}

export function FigurePanel({ figures }: { figures: Figure[] }) {
  const [shown, setShown] = useState<Figure | null>(null);
  if (!figures.length) {
    return (
      <section className="run-visuals" aria-label={copy.figures.panelTitle} data-count={0} data-testid="run-visuals">
        <p className="run-visuals-empty" data-testid="run-figures-empty">
          <ImageOff aria-hidden="true" />
          <span>{copy.figures.none}</span>
        </p>
      </section>
    );
  }
  return (
    <section className="run-visuals run-figures" aria-label={copy.figures.panelTitle} data-count={figures.length} data-testid="run-figures">
      {figures.map((figure) => (
        <figure key={figure.key} className="run-figure" data-origin={figure.origin}>
          <button type="button" className="run-figure-open" onClick={() => setShown(figure)} aria-label={copy.figures.open(figure.label)} data-testid="figure-open">
            <FigureImage figure={figure} big={false} />
            <span className="run-figure-zoom" aria-hidden="true">
              <Maximize2 />
            </span>
          </button>
          <figcaption>
            <span className="run-figure-origin">{ORIGIN_WORD[figure.origin]}</span>
            <span>{figure.label}</span>
            <Credit figure={figure} />
          </figcaption>
        </figure>
      ))}
      <Dialog.Root open={shown !== null} onOpenChange={(open) => (open ? null : setShown(null))}>
        <Dialog.Portal>
          <Dialog.Overlay className="figure-backdrop" />
          <Dialog.Content className="figure-full" aria-describedby={undefined} data-testid="figure-full">
            <div className="figure-full-head">
              <Dialog.Title asChild>
                <p>
                  <span className="run-figure-origin">{shown ? ORIGIN_WORD[shown.origin] : ""}</span> {shown?.label}
                </p>
              </Dialog.Title>
              <Dialog.Close asChild>
                <button type="button" className="btn run-btn" data-testid="figure-close">
                  <X aria-hidden="true" />
                  <span>{copy.figures.close}</span>
                </button>
              </Dialog.Close>
            </div>
            {shown ? <FigureImage figure={shown} big /> : null}
            {shown?.caption ? <p className="figure-full-caption">{shown.caption}</p> : null}
            {shown ? <Credit figure={shown} /> : null}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
