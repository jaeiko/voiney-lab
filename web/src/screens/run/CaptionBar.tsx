import * as Dialog from "@radix-ui/react-dialog";
import { MessagesSquare, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { run as copy } from "../../copy/ko";
import type { LogLine } from "../../voice/runModel";

// The caption (person's decision 2): the last words said or heard in at most
// two whole lines -- never cut. Words that do not fit in two lines are shown
// as subtitles are: a page of whole sentences at a time, the next after a
// moment, the last one staying. A press opens the conversation drawer with
// everything said and heard in this run.

const PAGE_MIN_MS = 2500;
const PAGE_MAX_MS = 7000;
const MS_PER_CHARACTER = 90;

/** Sentences (a Korean sentence ends in 다/요 and a stop, any in . ? !), each with its stop. */
export function sentencesOf(text: string): string[] {
  return text
    .split(/(?<=[.?!。])\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

/** Pages of whole sentences, each page fitting as `fits` says; a sentence too long alone is split between words. */
export function captionPages(text: string, fits: (candidate: string) => boolean): string[] {
  const pages: string[] = [];
  let page = "";
  const push = () => {
    if (page) pages.push(page);
    page = "";
  };
  for (const sentence of sentencesOf(text)) {
    const joined = page ? `${page} ${sentence}` : sentence;
    if (fits(joined)) {
      page = joined;
      continue;
    }
    push();
    if (fits(sentence)) {
      page = sentence;
      continue;
    }
    for (const word of sentence.split(/\s+/)) {
      const longer = page ? `${page} ${word}` : word;
      if (fits(longer) || !page) page = longer;
      else {
        push();
        page = word;
      }
    }
  }
  push();
  return pages.length ? pages : [text];
}

export function pageMs(page: string): number {
  return Math.min(PAGE_MAX_MS, Math.max(PAGE_MIN_MS, page.length * MS_PER_CHARACTER));
}

export function CaptionBar({ caption, log }: { caption: LogLine | null; log: LogLine[] }) {
  const [open, setOpen] = useState(false);
  const text = caption ? (caption.who === "heard" ? `“${caption.text}”` : caption.text) : copy.caption.empty;
  // Lane RX4 (decision 2): a reply's shown words that are the source for want of Korean (lane KO2's translation_missing,
  // nothing said aloud) are not shown large; what was said is the server's speech_text (KO2's spokenText).
  const lang = caption?.source ? "source" : "ko";
  const measurer = useRef<HTMLSpanElement>(null);
  const [pages, setPages] = useState<string[]>([text]);
  const [index, setIndex] = useState(0);
  const [width, setWidth] = useState(0);

  useLayoutEffect(() => {
    const node = measurer.current;
    if (!node) return;
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(() => setWidth(node.clientWidth)) : null;
    observer?.observe(node);
    setWidth(node.clientWidth);
    return () => observer?.disconnect();
  }, []);

  useLayoutEffect(() => {
    const node = measurer.current;
    const limit = node ? Number.parseFloat(getComputedStyle(node).lineHeight) * 2 + 1 : NaN;
    const fits = (candidate: string) => {
      if (!node || !Number.isFinite(limit) || node.clientWidth === 0) return true;
      node.textContent = candidate;
      return node.scrollHeight <= limit;
    };
    setPages(captionPages(text, fits));
    setIndex(0);
  }, [text, width]);

  useEffect(() => {
    if (index >= pages.length - 1) return;
    const timer = setTimeout(() => setIndex(index + 1), pageMs(pages[index]));
    return () => clearTimeout(timer);
  }, [index, pages]);

  const shown = pages[Math.min(index, pages.length - 1)] ?? text;
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button type="button" className="run-caption" data-who={caption?.who ?? "said"} data-lang={lang} aria-label={copy.caption.open} data-testid="run-caption">
          <MessagesSquare aria-hidden="true" />
          <span className="run-caption-box">
            <span className="run-caption-text" data-testid="run-caption-text" data-page={index + 1} data-pages={pages.length} aria-live="polite">
              {shown}
            </span>
            <span className="run-caption-text run-caption-measure" ref={measurer} aria-hidden="true" />
          </span>
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-backdrop" />
        <Dialog.Content className="drawer" aria-describedby={undefined} data-testid="run-drawer">
          <div className="drawer-head">
            <Dialog.Title asChild>
              <h2>{copy.caption.title}</h2>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button type="button" className="btn run-btn">
                <X aria-hidden="true" />
                <span>{copy.caption.close}</span>
              </button>
            </Dialog.Close>
          </div>
          {log.length ? (
            <ol className="drawer-log">
              {[...log].reverse().map((line) => (
                <li key={line.key} data-who={line.who}>
                  <span className="drawer-who">{line.who === "heard" ? copy.caption.heard : copy.caption.said}</span>
                  <span>{line.text}</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="muted">{copy.caption.empty}</p>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
