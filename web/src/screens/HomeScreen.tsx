import { ClipboardCheck, Hourglass, Play, RotateCcw, Upload } from "lucide-react";

import { useExperiments, useProtocolDetail, useProtocols, useSignOutOnExpiry, useWorkspaceSession } from "../api/queries";
import type { AuthSession, CatalogEntry, Experiment } from "../api/types";
import { BigCard, ErrorBox, LoadingBox } from "../components/Pieces";
import { home, states } from "../copy/ko";
import { oldRoutes, routes } from "../lib/navigation";
import { elapsedWords, entryAnalysing, reviewCount } from "../lib/protocolStatus";

export const RESUMABLE = new Set(["ready", "in_progress", "paused", "blocked"]);

export function latestResumable(experiments: Experiment[], principalId: string | null): Experiment | null {
  const mine = experiments.filter(
    (item) =>
      item &&
      typeof item.session_id === "string" &&
      RESUMABLE.has(item.status) &&
      (principalId === null || !item.owner_principal_id || item.owner_principal_id === principalId),
  );
  const when = (item: Experiment) => {
    const at = Date.parse(item.started_at ?? item.updated_at ?? "");
    return Number.isFinite(at) ? at : -Infinity;
  };
  return mine.sort((a, b) => when(b) - when(a))[0] ?? null;
}

export type Situation =
  | { kind: "resume"; experiment: Experiment; title: string }
  | { kind: "review"; entries: CatalogEntry[] }
  | { kind: "first" }
  | { kind: "analysing"; entry: CatalogEntry }
  | { kind: "start"; ready: number };

/** The one card the home shows, from the server's lists alone (docs/UI_SPEC.md 4.2). */
export function situationOf(entries: CatalogEntry[], experiments: Experiment[], principalId: string | null): Situation {
  const resume = latestResumable(experiments, principalId);
  if (resume) {
    const title =
      (typeof resume.protocol_title === "string" && resume.protocol_title.trim()) ||
      entries.find((entry) => entry.protocol_id === resume.protocol_id)?.title ||
      "";
    return { kind: "resume", experiment: resume, title };
  }
  const pending = entries.filter((entry) => reviewCount(entry) !== null);
  if (pending.length) return { kind: "review", entries: pending };
  if (!entries.length) return { kind: "first" };
  const ready = entries.filter((entry) => entry.available_for_execution === true).length;
  const analysing = entries.find((entry) => entryAnalysing(entry));
  if (!ready && analysing) return { kind: "analysing", entry: analysing };
  return { kind: "start", ready };
}

function SecondaryActions({ keysShown }: { keysShown: Array<keyof typeof home.secondary> }) {
  const hrefs: Record<keyof typeof home.secondary, string> = {
    protocols: routes.protocols,
    upload: oldRoutes.upload,
    records: oldRoutes.records,
  };
  return (
    <div className="secondary-actions">
      {keysShown.map((key) => (
        <a key={key} className="btn btn-quiet" href={hrefs[key]} data-secondary={key}>
          {home.secondary[key]}
        </a>
      ))}
    </div>
  );
}

function AnalysingCard({ entry }: { entry: CatalogEntry }) {
  const detail = useProtocolDetail(entry.protocol_id);
  const elapsed = elapsedWords(detail.data?.analysis_run?.requested_at);
  return (
    <BigCard kind="analysing" title={home.analysing} sub={home.analysingSub(entry.title, elapsed)} why={home.analysingWhy} icon={<Hourglass aria-hidden="true" />} />
  );
}

export function HomeScreen({ session }: { session: AuthSession }) {
  const protocolsQuery = useProtocols();
  const experimentsQuery = useExperiments();
  const workspace = useWorkspaceSession();
  useSignOutOnExpiry(protocolsQuery.error);

  const name = session.account?.name?.trim();
  const greeting = session.mode === "accounts" && name ? home.greeting(name) : home.greetingAnonymous;

  let body;
  if (protocolsQuery.isPending) {
    body = <LoadingBox />;
  } else if (protocolsQuery.isError) {
    body = <ErrorBox message={states.protocolsFailed} onRetry={() => void protocolsQuery.refetch()} />;
  } else {
    // The experiments list may be unavailable (the workspace off): the home then draws from the protocols alone.
    const experiments = experimentsQuery.data ?? [];
    const principal = workspace.data?.principal_id ?? null;
    const situation = situationOf(protocolsQuery.data, experiments, principal);
    switch (situation.kind) {
      case "resume": {
        const step = situation.experiment.current_step_label;
        body = (
          <>
            <BigCard
              kind="resume"
              title={home.resume}
              sub={home.resumeSub(situation.title, typeof step === "string" && step.trim() ? step.trim() : null)}
              href={oldRoutes.experiment}
              icon={<RotateCcw aria-hidden="true" />}
            />
            <SecondaryActions keysShown={["protocols", "records"]} />
          </>
        );
        break;
      }
      case "review": {
        const one = situation.entries.length === 1 ? situation.entries[0] : null;
        const total = situation.entries.reduce((sum, entry) => sum + (reviewCount(entry) ?? 0), 0);
        body = (
          <>
            <BigCard
              kind="review"
              title={home.review}
              sub={one ? home.reviewOneSub(one.title, reviewCount(one) ?? 0) : home.reviewManySub(situation.entries.length)}
              why={home.reviewWhy(total)}
              href={one ? oldRoutes.protocol(one.protocol_id) : routes.protocols}
              icon={<ClipboardCheck aria-hidden="true" />}
            />
            <SecondaryActions keysShown={["protocols", "upload"]} />
          </>
        );
        break;
      }
      case "first":
        body = (
          <>
            <BigCard kind="first" title={home.first} sub={home.firstSub} href={oldRoutes.upload} icon={<Upload aria-hidden="true" />} />
            <SecondaryActions keysShown={["records"]} />
          </>
        );
        break;
      case "analysing":
        body = (
          <>
            <AnalysingCard entry={situation.entry} />
            <SecondaryActions keysShown={["upload", "records"]} />
          </>
        );
        break;
      case "start":
        body = (
          <>
            <BigCard kind="start" title={home.start} sub={home.startSub(situation.ready)} href={oldRoutes.experiment} icon={<Play aria-hidden="true" />} />
            <SecondaryActions keysShown={["protocols", "upload", "records"]} />
          </>
        );
        break;
    }
  }

  return (
    <section aria-labelledby="home-title">
      <h1 id="home-title" className="screen-title" style={{ marginBottom: "var(--space-6)" }}>
        {greeting}
      </h1>
      {body}
    </section>
  );
}
