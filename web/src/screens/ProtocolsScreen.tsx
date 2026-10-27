import { ClipboardCheck, Hourglass, Play, RotateCcw, Upload, type LucideIcon } from "lucide-react";

import { useProtocolDetail, useProtocols, useSignOutOnExpiry } from "../api/queries";
import type { CatalogEntry } from "../api/types";
import { Chip, EmptyBox, ErrorBox, LoadingBox } from "../components/Pieces";
import { protocols as words, states } from "../copy/ko";
import { oldRoutes, routes, startHref } from "../lib/navigation";
import { entryAnalysing, protocolStatus, sortEntries, uploadedDay } from "../lib/protocolStatus";
import { displayTitle } from "../lib/protocolTitle";

function ProtocolRow({ entry }: { entry: CatalogEntry }) {
  // Only a row still being analysed asks the server for its run (elapsed time, pieces).
  const detail = useProtocolDetail(entry.protocol_id, entryAnalysing(entry));
  const status = protocolStatus(entry, detail.data?.analysis_run ?? null);
  const meta = [
    entry.source_filename?.trim() || null,
    Number.isInteger(entry.step_count) && (entry.step_count as number) > 0 ? words.steps(entry.step_count as number) : null,
    uploadedDay(entry.created_at) ? words.uploaded(uploadedDay(entry.created_at) as string) : null,
  ].filter(Boolean);
  // The start opens lane RX2's start flow; the cards, a failed analysis and a
  // running one open lane RX1's screens; anything else the old detail.
  const href =
    status.action === "review"
      ? routes.review(entry.protocol_id)
      : status.action === "retry"
        ? routes.analysis(entry.protocol_id)
        : status.kind === "analysing"
          ? routes.analysis(entry.protocol_id)
          : status.action === "start"
            ? startHref(entry.protocol_id)
            : oldRoutes.protocol(entry.protocol_id);
  const actionLabel = status.action ? words.actions[status.action] : status.kind === "analysing" ? words.actions.progress : null;
  // Lane RX4 (decision 4): the action's icon beside its word.
  const ActionIcon: LucideIcon = status.action === "start" ? Play : status.action === "review" ? ClipboardCheck : status.action === "retry" ? RotateCcw : Hourglass;
  return (
    <li className="row" data-protocol-id={entry.protocol_id} data-status={status.kind}>
      <div className="row-head">
        <a className="row-title" href={oldRoutes.protocol(entry.protocol_id)}>{displayTitle(entry)}</a>
        {status.chips.map((chip, index) => (
          <Chip key={`${chip.kind}-${index}`} chip={chip} />
        ))}
      </div>
      {meta.length ? <p className="row-meta">{meta.join(" · ")}</p> : null}
      {actionLabel ? (
        <div className="row-action">
          <a className={`btn ${status.action === "start" ? "btn-primary" : status.action ? "" : "btn-quiet"}`} href={href} data-action={status.action ?? "progress"}>
            <ActionIcon aria-hidden="true" />
            <span>{actionLabel}</span>
          </a>
        </div>
      ) : null}
    </li>
  );
}

export function ProtocolsScreen() {
  const query = useProtocols();
  useSignOutOnExpiry(query.error);

  let body;
  if (query.isPending) {
    body = <LoadingBox />;
  } else if (query.isError) {
    body = <ErrorBox message={states.protocolsFailed} onRetry={() => void query.refetch()} />;
  } else if (!query.data.length) {
    body = (
      <EmptyBox
        message={words.empty}
        action={
          <a className="btn btn-primary" href={routes.upload} style={{ marginTop: "var(--space-4)" }}>
            {words.emptyAction}
          </a>
        }
      />
    );
  } else {
    body = (
      <ul className="row-list" aria-label={words.title}>
        {sortEntries(query.data).map((entry) => (
          <ProtocolRow key={entry.protocol_id} entry={entry} />
        ))}
      </ul>
    );
  }

  const total = query.data?.length ?? 0;
  const ready = query.data?.filter((entry) => entry.available_for_execution === true).length ?? 0;

  return (
    <section aria-labelledby="protocols-title">
      <div className="screen-head">
        <div>
          <h1 id="protocols-title" className="screen-title">
            {words.title}
          </h1>
          {total ? (
            <p className="muted" data-testid="protocols-count">
              {words.count(total, ready)}
            </p>
          ) : null}
        </div>
        <a className="btn" href={routes.upload} data-testid="protocols-upload">
          <Upload aria-hidden="true" />
          {words.upload}
        </a>
      </div>
      {body}
    </section>
  );
}
