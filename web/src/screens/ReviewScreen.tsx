import { useQueryClient } from "@tanstack/react-query";
import { Check, ChevronLeft, ChevronRight, ExternalLink } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError, postJson } from "../api/client";
import { keys, protocolChanged, protocolPath, sourcePageImageUrl, useReviewDraft, useSignOutOnExpiry, useSourcePage } from "../api/queries";
import type { AuthSession, CardChoice, CardValue, ReviewCard, ReviewDraft, SourceLine } from "../api/types";
import { ChoiceCard, type Written } from "../components/ChoiceCard";
import { PageImage, spanToBand, type Band } from "../components/PageImage";
import { ErrorBox, LoadingBox, Note } from "../components/Pieces";
import { TitleLine } from "../components/TitleLine";
import { review as words } from "../copy/ko";
import { goTo, routes, startHref } from "../lib/navigation";
import { cardLines, cardPage, cardsOf, firstCardIndex, needsOf, nextUndecided, pickExtends, undecidedCount } from "../lib/reviewCards";

interface Picked {
  cardId: string;
  page: number;
  first: number;
  last: number;
}

type Done = { kind: "none" } | { kind: "registering" } | { kind: "registered" } | { kind: "name" } | { kind: "failed"; message: string };

/**
 * The confirmation screen (docs/UI_SPEC.md 4.6; lane RX1, decision 2): the
 * source page on the left with a band where the card's lines stand, one
 * card on the right with the server's buttons, "확인할 것 n / N", [이전] and
 * [나중에 하기]. When every card is decided the review is registered and
 * "확인 끝 — 이제 실험할 수 있어요 [실험 시작]" shows. The whole source is never
 * listed; [원문 열기] opens the page itself.
 */
export function ReviewScreen({ protocolId, session }: { protocolId: string; session: AuthSession }) {
  const client = useQueryClient();
  const draft = useReviewDraft(protocolId);
  useSignOutOnExpiry(draft.error);
  const cards = cardsOf(draft.data);
  const [index, setIndex] = useState<number | null>(null);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [pageShown, setPageShown] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [done, setDone] = useState<Done>({ kind: "none" });
  const [reviewerName, setReviewerName] = useState("");

  // The first undecided card when the draft arrives (once per protocol).
  const loadedFor = draft.data?.protocol_id ?? null;
  useEffect(() => {
    if (loadedFor === protocolId && index === null && cards.length) {
      const first = firstCardIndex(draft.data);
      setIndex(first ?? cards.length - 1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadedFor, protocolId]);

  const card: ReviewCard | null = index !== null ? (cards[index] ?? null) : null;
  const page = pageShown ?? cardPage(card);
  const sourcePage = useSourcePage(protocolId, page);
  const lines: SourceLine[] = sourcePage.data?.lines ?? [];
  const allDecided = cards.length > 0 && undecidedCount(cards) === 0;

  function showCard(next: number | null) {
    setIndex(next);
    setPageShown(null);
    setPicked(null);
    setNote(null);
  }

  function replaceDraft(answer: Partial<ReviewDraft>) {
    client.setQueryData<ReviewDraft>(keys.reviewDraft(protocolId), (current) =>
      current ? { ...current, ...answer, cards: Array.isArray(answer.cards) ? answer.cards : current.cards } : current,
    );
  }

  async function decide(choice: CardChoice, written: Written, value?: CardValue) {
    if (!card || !draft.data || busy) return;
    if (choice.route === "reanalysis") {
      setBusy(true);
      setNote(words.reanalysing);
      try {
        await postJson(protocolPath(protocolId, "/reanalysis"), {});
      } catch (failure) {
        setBusy(false);
        setNote(failure instanceof ApiError && failure.messageKo ? failure.messageKo : words.decideFailed);
        return;
      }
      protocolChanged(client, protocolId);
      goTo(routes.analysis(protocolId));
      return;
    }
    const request = choice.request;
    if (!request) return;
    const needs = needsOf(choice);
    const body: Record<string, unknown> = { ...request, target: draft.data.target };
    if (needs.lines) {
      if (!picked || picked.cardId !== card.card_id) {
        setNote(words.needLines);
        return;
      }
      body.source_lines = { page: picked.page, first_line: picked.first, last_line: picked.last };
    }
    if (written.text !== undefined) body.text = written.text;
    if (written.minutes !== undefined) body.seconds = Math.round(written.minutes * 60);
    if (written.memo !== undefined) body.memo = written.memo;
    void value;
    setBusy(true);
    setNote(null);
    try {
      const answer = await postJson<Partial<ReviewDraft>>(protocolPath(protocolId, "/review-decisions"), body);
      setBusy(false);
      if (Array.isArray(answer.cards)) {
        replaceDraft(answer);
        const next = nextUndecided(answer.cards, index ?? 0);
        setPicked(null);
        setPageShown(null);
        if (next !== null) setIndex(next);
      } else {
        await draft.refetch();
      }
    } catch (failure) {
      setBusy(false);
      if (failure instanceof ApiError && failure.code === "review_target_changed") {
        await draft.refetch();
        setNote(words.changed);
        return;
      }
      setNote(failure instanceof ApiError && failure.messageKo ? failure.messageKo : words.decideFailed);
    }
  }

  async function memo(text: string) {
    if (!card || !draft.data) return;
    setBusy(true);
    try {
      const answer = await postJson<Partial<ReviewDraft>>(protocolPath(protocolId, "/review-decisions"), {
        item_id: card.item_id,
        action: "memo",
        memo: text,
        target: draft.data.target,
      });
      if (Array.isArray(answer.cards)) replaceDraft(answer);
    } catch (failure) {
      setNote(failure instanceof ApiError && failure.messageKo ? failure.messageKo : words.decideFailed);
    }
    setBusy(false);
  }

  async function register(name?: string) {
    if (!draft.data) return;
    setDone({ kind: "registering" });
    const body: Record<string, unknown> = { target: draft.data.target };
    const accountName = session.mode === "accounts" ? session.account?.name : null;
    if (name) body.reviewer_name = name;
    else if (!accountName && reviewerName.trim()) body.reviewer_name = reviewerName.trim();
    try {
      await postJson(protocolPath(protocolId, "/review-versions"), body);
    } catch (failure) {
      if (failure instanceof ApiError && failure.code === "reviewer_name_required") {
        setDone({ kind: "name" });
        return;
      }
      setDone({ kind: "failed", message: failure instanceof ApiError && failure.messageKo ? failure.messageKo : words.registerFailed });
      return;
    }
    setDone({ kind: "registered" });
    protocolChanged(client, protocolId);
  }

  // Every card decided on an analysis still waiting: the review becomes the
  // version that runs, once. A draft read back later (a registered version,
  // whose cards are all decided) is never registered again from here.
  const pending = Boolean(draft.data?.target && draft.data.review_pending);
  const canRegister = pending && draft.data?.registrable === true;
  useEffect(() => {
    if (allDecided && canRegister && done.kind === "none") void register();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allDecided, canRegister]);

  function sendName(event: FormEvent) {
    event.preventDefault();
    if (reviewerName.trim()) void register(reviewerName.trim());
  }

  if (draft.isPending) return <LoadingBox label={words.loading} />;
  if (draft.isError) return <ErrorBox message={words.loadFailed} onRetry={() => void draft.refetch()} />;
  const data = draft.data;
  const head = (
    <>
      <p className="screen-why">{words.why}</p>
      <TitleLine entry={{ protocol_id: protocolId, title: data.title, source_filename: data.source_filename ?? null }} />
    </>
  );

  if (!data.target) {
    return (
      <section aria-labelledby="protocol-title">
        {head}
        <div className="state-box" data-state="empty">
          <p>{data.available_for_execution ? words.empty : words.notYet}</p>
          {data.available_for_execution ? (
            <a className="btn btn-primary" href={startHref(protocolId)} data-testid="review-start">
              {words.start}
            </a>
          ) : null}
        </div>
      </section>
    );
  }

  // Once the registration began, its outcome is the screen whatever a later read of the draft says.
  if (allDecided || !cards.length || done.kind !== "none") {
    let box;
    if (!cards.length || done.kind === "registered" || (allDecided && !pending && done.kind === "none")) {
      // Nothing to confirm, just registered, or a registered version read back: the start.
      box = (
        <div className="state-box" data-state="done" data-testid="review-done">
          <p>
            <Check aria-hidden="true" />
            <span>{cards.length ? words.allDoneHeading : words.empty}</span>
          </p>
          {cards.length ? <p className="muted">{words.allDoneSub}</p> : null}
          <div className="card-choices">
            <a className="btn btn-primary" href={startHref(protocolId)} data-testid="review-start">
              {words.start}
            </a>
          </div>
        </div>
      );
    } else if (done.kind === "name") {
      box = (
        <form className="state-box" data-state="name" onSubmit={sendName} noValidate>
          <p>{words.nameAsk}</p>
          <label className="field">
            {words.nameLabel}
            <input type="text" value={reviewerName} autoFocus onChange={(event) => setReviewerName(event.target.value)} data-testid="reviewer-name" />
          </label>
          <button type="submit" className="btn btn-primary" disabled={!reviewerName.trim()}>
            {words.start}
          </button>
        </form>
      );
    } else if (done.kind === "failed") {
      box = <ErrorBox message={done.message} onRetry={() => void register()} />;
    } else if (allDecided && pending && !canRegister && done.kind === "none") {
      // Every card decided, yet the server will not register (a safety number still differs from the source): its own sentences.
      box = (
        <div className="state-box" data-state="blocked" role="alert" data-testid="review-blocked">
          {(data.registration_blockers ?? []).map((blocker, index) => (
            <p key={index}>{blocker.message_ko}</p>
          ))}
          <a className="btn btn-quiet" href={routes.protocols} data-testid="review-later">
            {words.later}
          </a>
        </div>
      );
    } else {
      box = <LoadingBox label={words.registering} />;
    }
    return (
      <section aria-labelledby="protocol-title">
        {head}
        {box}
      </section>
    );
  }

  if (!card) return <LoadingBox label={words.loading} />;

  const needsLines = card.choices.some((choice) => needsOf(choice).lines);
  const bands: Band[] = [];
  const span = cardLines(card);
  const pickedHere = picked && picked.cardId === card.card_id && picked.page === page ? picked : null;
  // The card's lines, and the lines a person picked; the same lines once, as picked.
  if (span && span.page === page && !(pickedHere && pickedHere.first === span.first_line && pickedHere.last === span.last_line)) {
    bands.push(spanToBand(span, "card") as Band);
  }
  if (pickedHere) bands.push({ first: pickedHere.first, last: pickedHere.last, kind: "picked" });
  const decidedCount = cards.length - undecidedCount(cards);
  const pageCount = sourcePage.data?.page_count ?? null;

  return (
    <section aria-labelledby="protocol-title" className="review-screen">
      {head}
      <div className="review-progress" data-testid="review-progress">
        <span>{words.progress(decidedCount, cards.length)}</span>
        <progress value={decidedCount} max={cards.length} aria-label={words.progress(decidedCount, cards.length)} />
      </div>
      <div className="review-columns">
        <div className="review-source">
          {page ? (
            <>
              <div className="review-source-head">
                <span className="row-title">{words.sourcePage(page)}</span>
                <a className="btn btn-quiet" href={sourcePageImageUrl(protocolId, page)} target="_blank" rel="noopener noreferrer">
                  <ExternalLink aria-hidden="true" />
                  {words.openSource}
                </a>
              </div>
              <PageImage src={sourcePageImageUrl(protocolId, page)} page={page} lines={lines} imageDpi={sourcePage.data?.image_dpi ?? 120} bands={bands} />
              {pageCount && pageCount > 1 ? (
                <div className="card-choices">
                  <button type="button" className="btn btn-quiet" disabled={page <= 1} onClick={() => setPageShown(page - 1)}>
                    <ChevronLeft aria-hidden="true" />
                    {words.prevPage}
                  </button>
                  <button type="button" className="btn btn-quiet" disabled={page >= pageCount} onClick={() => setPageShown(page + 1)}>
                    {words.nextPage}
                    <ChevronRight aria-hidden="true" />
                  </button>
                </div>
              ) : null}
              {needsLines ? (
                sourcePage.isPending ? (
                  <LoadingBox label={words.pageLoading} />
                ) : (
                  <div className="review-lines" data-testid="review-lines">
                    <p className="muted">{picked && picked.cardId === card.card_id ? words.pickedLines(picked.page, picked.first, picked.last) : words.pickLines}</p>
                    <ol>
                      {lines.map((line) => {
                        const inPick = picked && picked.cardId === card.card_id && picked.page === page && line.number >= picked.first && line.number <= picked.last;
                        const suggested = span && span.page === page && line.number >= span.first_line && line.number <= span.last_line;
                        return (
                          <li key={line.number}>
                            <button
                              type="button"
                              className="review-line"
                              aria-pressed={Boolean(inPick)}
                              data-suggested={suggested ? "true" : undefined}
                              data-line={line.number}
                              onClick={(event) => setPicked({ cardId: card.card_id, ...pickExtends(picked && picked.cardId === card.card_id ? picked : null, page, line.number, event.shiftKey) })}
                            >
                              <span className="review-line-number">{line.number}</span>
                              <span>{line.text}</span>
                            </button>
                          </li>
                        );
                      })}
                    </ol>
                  </div>
                )
              ) : null}
            </>
          ) : (
            <p className="muted">{words.noPage}</p>
          )}
        </div>
        <div className="review-card">
          <ChoiceCard card={card} busy={busy} note={note} onChoose={(choice, written, value) => void decide(choice, written, value)} onMemo={(text) => void memo(text)} />
          <div className="review-nav">
            <button type="button" className="btn btn-quiet" disabled={busy || index === null || index <= 0} onClick={() => showCard((index ?? 1) - 1)} data-testid="review-prev">
              <ChevronLeft aria-hidden="true" />
              {words.prev}
            </button>
            {card.decided ? (
              <button type="button" className="btn btn-quiet" disabled={busy || index === null || index >= cards.length - 1} onClick={() => showCard((index ?? 0) + 1)} data-testid="review-next">
                {words.next}
                <ChevronRight aria-hidden="true" />
              </button>
            ) : null}
            <a className="btn btn-quiet" href={routes.protocols} data-testid="review-later">
              {words.later}
            </a>
          </div>
          {note && card.decided ? <Note>{note}</Note> : null}
        </div>
      </div>
    </section>
  );
}
