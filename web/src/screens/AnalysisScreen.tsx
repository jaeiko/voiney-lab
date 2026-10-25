import { useQueryClient } from "@tanstack/react-query";
import { CircleAlert, ClipboardCheck, Hourglass, Play, RotateCcw, Upload } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError, postJson } from "../api/client";
import { protocolChanged, protocolPath, useProtocolDetail, useSignOutOnExpiry } from "../api/queries";
import { ErrorBox, LoadingBox, Note } from "../components/Pieces";
import { TitleLine } from "../components/TitleLine";
import { analysis as words } from "../copy/ko";
import { elapsedClock, estimatedMinutesLeft, estimateWords, failureWords, pieceLines, runPhase } from "../lib/analysisProgress";
import { routes, startHref } from "../lib/navigation";

/** The clock ticks once a second while the screen is open; the server's time is the start. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

/**
 * Waiting for the analysis (docs/UI_SPEC.md 4.5; lane RX1, decision 1): the
 * elapsed clock, the pieces with their pages, how much longer at the pace
 * so far (only from measured progress, said to be a guess), and that
 * leaving the screen is fine. When it ends: 실험 시작, 확인하기 or 다시 분석하기.
 */
export function AnalysisScreen({ protocolId }: { protocolId: string }) {
  const client = useQueryClient();
  const detail = useProtocolDetail(protocolId);
  useSignOutOnExpiry(detail.error);
  const phase = runPhase(detail.data);
  const now = useNow(phase === "running" || phase === "ocr");
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  async function retry() {
    setRetrying(true);
    setRetryError(null);
    try {
      await postJson(protocolPath(protocolId, "/analysis"), {});
    } catch (failure) {
      setRetrying(false);
      setRetryError(failure instanceof ApiError && failure.messageKo ? failure.messageKo : words.retryFailed);
      return;
    }
    setRetrying(false);
    protocolChanged(client, protocolId);
    void detail.refetch();
  }

  if (detail.isPending) return <LoadingBox />;
  if (detail.isError) {
    const message = detail.error instanceof ApiError && detail.error.status === 404 ? words.notFound : words.loadFailed;
    return <ErrorBox message={message} onRetry={() => void detail.refetch()} />;
  }
  const entry = detail.data;
  const run = entry.analysis_run ?? null;
  const renamable = entry.development_only !== true;

  let body;
  switch (phase) {
    case "ready":
      body = (
        <div className="state-box" data-state="done" data-testid="analysis-ready">
          <p>
            <Play aria-hidden="true" />
            <span>{words.readyHeading}</span>
          </p>
          <p className="muted">{words.readySub}</p>
          <div className="card-choices">
            <a className="btn btn-primary" href={startHref(protocolId)} data-testid="analysis-start">
              {words.start}
            </a>
            <a className="btn btn-quiet" href={routes.protocols}>
              {words.toList}
            </a>
          </div>
        </div>
      );
      break;
    case "review": {
      const count = entry.review_pending?.needs_review_count ?? 0;
      body = (
        <div className="state-box" data-state="review" data-testid="analysis-review">
          <p>
            <ClipboardCheck aria-hidden="true" />
            <span>{words.reviewHeading(count)}</span>
          </p>
          <p className="muted">{words.reviewSub}</p>
          <div className="card-choices">
            <a className="btn btn-primary" href={routes.review(protocolId)} data-testid="analysis-review-link">
              {words.review}
            </a>
            <a className="btn btn-quiet" href={routes.protocols}>
              {words.toList}
            </a>
          </div>
        </div>
      );
      break;
    }
    case "failed":
      body = (
        <div className="state-box" data-state="error" role="alert" data-testid="analysis-failed">
          <p>
            <CircleAlert aria-hidden="true" />
            <span>{words.failedHeading}</span>
          </p>
          <p className="muted">{failureWords(run?.failure_code)}</p>
          {retryError ? <Note>{retryError}</Note> : null}
          <div className="card-choices">
            <button type="button" className="btn btn-primary" disabled={retrying} onClick={() => void retry()} data-testid="analysis-retry">
              <RotateCcw aria-hidden="true" />
              {retrying ? words.retrying : words.retry}
            </button>
            <a className="btn btn-quiet" href={routes.upload}>
              <Upload aria-hidden="true" />
              {words.uploadOther}
            </a>
          </div>
        </div>
      );
      break;
    case "blocked":
      body = (
        <div className="state-box" data-state="blocked">
          <p>
            <CircleAlert aria-hidden="true" />
            <span>{words.blockedHeading}</span>
          </p>
          <p className="muted">{words.blockedSub}</p>
          <div className="card-choices">
            <a className="btn btn-quiet" href={routes.protocols}>
              {words.toList}
            </a>
          </div>
        </div>
      );
      break;
    case "idle":
      // Registered, nothing running yet: the analysis is started from here.
      body = (
        <div className="state-box" data-state="idle">
          <p>
            <Hourglass aria-hidden="true" />
            <span>{words.heading}</span>
          </p>
          {retryError ? <Note>{retryError}</Note> : null}
          <div className="card-choices">
            <button type="button" className="btn btn-primary" disabled={retrying} onClick={() => void retry()} data-testid="analysis-start-first">
              {retrying ? words.retrying : words.startAnalysis}
            </button>
            <a className="btn btn-quiet" href={routes.protocols}>
              {words.toList}
            </a>
          </div>
        </div>
      );
      break;
    default: {
      const clock = elapsedClock(run?.requested_at, now);
      const total = run?.total_chunks ?? 0;
      const done = run?.completed_chunks ?? 0;
      const pieces = pieceLines(run?.chunks);
      const estimate = estimateWords(estimatedMinutesLeft(run, now));
      body = (
        <div className="state-box" data-state="running" role="status" aria-live="polite" data-testid="analysis-running">
          <p>
            <Hourglass aria-hidden="true" />
            <span>{phase === "ocr" ? words.ocr : clock ? words.elapsed(clock) : words.heading}</span>
          </p>
          {total > 1 ? (
            <p className="muted" data-testid="analysis-pieces">
              {words.pieces(done, total)}
            </p>
          ) : null}
          {pieces.length > 1 ? (
            <ol className="piece-list">
              {pieces.map((piece, index) => (
                <li key={index} data-status={piece.status}>
                  {piece.text}
                </li>
              ))}
            </ol>
          ) : null}
          {estimate ? (
            <p className="muted" data-testid="analysis-estimate">
              {estimate}
            </p>
          ) : null}
          <p className="muted">{words.leave}</p>
          <div className="card-choices">
            <a className="btn btn-quiet" href={routes.protocols}>
              {words.toList}
            </a>
          </div>
        </div>
      );
    }
  }

  return (
    <section aria-labelledby="protocol-title">
      <p className="screen-why">{words.why}</p>
      <TitleLine entry={entry} renamable={renamable} />
      {entry.source_filename ? <p className="muted">{entry.source_filename}</p> : null}
      {body}
    </section>
  );
}
