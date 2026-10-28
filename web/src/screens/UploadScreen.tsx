import { useQueryClient } from "@tanstack/react-query";
import { FileUp, Link2 } from "lucide-react";
import { useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";

import { ApiError, postFile, postJson } from "../api/client";
import { protocolChanged, protocolPath, useSourcePages } from "../api/queries";
import type { AuthSession, CatalogEntry, UploadResponse } from "../api/types";
import { PagePicker } from "../components/PagePicker";
import { LoadingBox, Note } from "../components/Pieces";
import { upload as words } from "../copy/ko";
import { goTo, routes } from "../lib/navigation";
import { FAILED_STATES, RUNNING_STATES } from "../lib/protocolStatus";
import { ACCEPTED_EXTENSIONS, sourceMediaType } from "../lib/sourceMedia";

type Phase =
  | { kind: "pick" }
  | { kind: "uploading"; name: string }
  | { kind: "pages"; entry: CatalogEntry; duplicate: string | null; picking: boolean }
  | { kind: "starting"; entry: CatalogEntry };
type SourceStatus = "yes" | "no" | "unknown";

export function uploadErrorWords(error: unknown): string {
  if (!(error instanceof ApiError)) return words.errors.unknown;
  if (error.messageKo) return error.messageKo;
  const table = words.errors as Record<string, string>;
  return table[error.code] ?? words.errors.unknown;
}

/**
 * After the registration: the analysis already ran, is running, or the
 * server's OCR will start it -- then the progress screen; otherwise the
 * page question (lane RX1, decision 1).
 */
export function afterUpload(answer: UploadResponse): "analysis" | "pages" {
  const entry = answer.protocol;
  const status = entry.analysis_status ?? "";
  if (entry.available_for_execution === true) return "analysis";
  if (answer.ocr?.automatic === true) return "analysis";
  if (RUNNING_STATES.has(status) || status === "ocr_in_progress") return "analysis";
  if (entry.review_pending) return "analysis";
  if (FAILED_STATES.has(status)) return "pages";
  return "pages";
}

/**
 * The upload (docs/UI_SPEC.md 4.4; lane RX1, decision 1): one file or one
 * public link, then on the same screen "어느 쪽을 분석할까요?" -- the whole
 * document (recommended) or a start and an end page picked on small
 * pictures of the pages -- then the analysis starts and the progress
 * screen opens.
 */
export function UploadScreen({ session }: { session: AuthSession }) {
  const client = useQueryClient();
  const input = useRef<HTMLInputElement | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "pick" });
  const [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState("");
  const [dragging, setDragging] = useState(false);
  const [sourceStatus, setSourceStatus] = useState<SourceStatus | null>(null);
  const [sourceNote, setSourceNote] = useState("");
  const entryId = phase.kind === "pages" || phase.kind === "starting" ? phase.entry.protocol_id : null;
  const pages = useSourcePages(entryId, phase.kind === "pages" && phase.picking);
  const signedIn = session.mode === "dev" || session.signed_in;

  function accept(answer: UploadResponse) {
    const entry = answer.protocol;
    protocolChanged(client, entry.protocol_id);
    if (afterUpload(answer) === "analysis") {
      goTo(routes.analysis(entry.protocol_id));
      return;
    }
    const duplicate = answer.deduplicated ? (answer.review_suggestion?.message_ko ?? words.duplicate(entry.source_filename ?? "")) : null;
    setPhase({ kind: "pages", entry, duplicate, picking: false });
  }

  async function sendFile(file: File) {
    if (!sourceStatus) {
      setError(words.sourceRequired);
      return;
    }
    const mediaType = sourceMediaType(file);
    if (!mediaType) {
      setError(words.errors.unsupported_pdf_media_type);
      return;
    }
    setError(null);
    setPhase({ kind: "uploading", name: file.name });
    try {
      const answer = await postFile<UploadResponse>(
        `/api/protocols?filename=${encodeURIComponent(file.name)}`,
        file,
        mediaType,
        {
          "X-Voiney-Source-Status": sourceStatus,
          "X-Voiney-Source-Note": encodeURIComponent(sourceNote.trim()),
        },
      );
      accept(answer);
    } catch (failure) {
      setPhase({ kind: "pick" });
      setError(uploadErrorWords(failure));
    }
  }

  function onPicked(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) void sendFile(file);
  }

  function onDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) void sendFile(file);
  }

  async function sendLink(event: FormEvent) {
    event.preventDefault();
    const url = link.trim();
    if (!url) return;
    if (!sourceStatus) {
      setError(words.sourceRequired);
      return;
    }
    setError(null);
    setPhase({ kind: "uploading", name: words.importing });
    try {
      const answer = await postJson<UploadResponse>("/api/protocols/import-link", {
        url,
        public_source_status: sourceStatus,
        source_note: sourceNote.trim(),
      });
      accept(answer);
    } catch (failure) {
      setPhase({ kind: "pick" });
      setError(uploadErrorWords(failure));
    }
  }

  async function startAnalysis(entry: CatalogEntry, range: { first: number; last: number } | null) {
    setError(null);
    setPhase({ kind: "starting", entry });
    const query = range ? `?first_page=${range.first}&last_page=${range.last}` : "";
    try {
      await postJson(protocolPath(entry.protocol_id, `/analysis${query}`), {});
    } catch (failure) {
      setPhase({ kind: "pages", entry, duplicate: null, picking: Boolean(range) });
      setError(failure instanceof ApiError && failure.code === "analysis_page_range_invalid" ? words.errors.analysis_page_range_invalid : uploadErrorWords(failure) === words.errors.unknown ? words.startFailed : uploadErrorWords(failure));
      return;
    }
    protocolChanged(client, entry.protocol_id);
    goTo(routes.analysis(entry.protocol_id));
  }

  let body;
  if (phase.kind === "uploading") {
    body = <LoadingBox label={words.uploading(phase.name)} />;
  } else if (phase.kind === "starting") {
    body = <LoadingBox label={words.starting} />;
  } else if (phase.kind === "pages") {
    const entry = phase.entry;
    body = (
      <div className="pages-ask" data-testid="pages-ask">
        {phase.duplicate ? <p className="muted">{phase.duplicate}</p> : null}
        <p className="muted">{words.pagesWhy}</p>
        <h2 className="choice-card-question">{words.pagesAsk}</h2>
        {!phase.picking ? (
          <div className="pages-options">
            <button type="button" className="big-card big-card-button" data-card="all" onClick={() => void startAnalysis(entry, null)} data-testid="pages-all">
              <span className="big-card-title">
                <span>{words.pagesAll}</span>
              </span>
              <span className="big-card-sub">{words.pagesAllHint}</span>
            </button>
            <button type="button" className="big-card big-card-button" data-card="pick" onClick={() => setPhase({ ...phase, picking: true })} data-testid="pages-pick">
              <span className="big-card-title">
                <span>{words.pagesPick}</span>
              </span>
              <span className="big-card-sub">{words.pagesPickHint}</span>
            </button>
          </div>
        ) : pages.isPending ? (
          <LoadingBox label={words.pagesLoading} />
        ) : pages.isError ? (
          <Note>{words.pagesFailed}</Note>
        ) : (
          <>
            <p className="muted">{words.pagesCount(pages.data.page_count)}</p>
            <PagePicker protocolId={entry.protocol_id} pageCount={pages.data.page_count} onChoose={(first, last) => void startAnalysis(entry, { first, last })} onReset={() => setPhase({ ...phase, picking: false })} />
          </>
        )}
        {error ? <Note testId="upload-error">{error}</Note> : null}
      </div>
    );
  } else {
    body = (
      <>
        <fieldset className="choice-card source-disclosure" data-testid="source-disclosure">
          <legend className="choice-card-question">{words.sourceQuestion}</legend>
          <p className="muted">{words.sourceWhy}</p>
          <div className="segmented">
            {(Object.entries(words.sourceOptions) as Array<[SourceStatus, string]>).map(([value, label]) => (
              <label key={value} className="segmented-option" data-checked={sourceStatus === value}>
                <input type="radio" name="public-source-status" value={value} checked={sourceStatus === value} onChange={() => { setSourceStatus(value); setError(null); }} />
                <span>{label}</span>
              </label>
            ))}
          </div>
          <label className="field">
            {words.sourceNote}
            <textarea value={sourceNote} maxLength={500} rows={3} onChange={(event) => setSourceNote(event.target.value)} />
          </label>
          <p className="muted">{words.sourceNoteHint}</p>
        </fieldset>
        <section
          className="drop-zone"
          data-dragging={dragging ? "true" : undefined}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <FileUp aria-hidden="true" />
          <p className="lead">{words.drop}</p>
          <input ref={input} type="file" accept={ACCEPTED_EXTENSIONS} className="sr-only" onChange={onPicked} data-testid="file-input" aria-label={words.pick} />
          <button type="button" className="btn btn-primary btn-wide" onClick={() => input.current?.click()} data-testid="file-pick">
            {words.pick}
          </button>
          <p className="muted">{words.formats}</p>
        </section>
        {error ? <Note testId="upload-error">{error}</Note> : null}
        <form className="link-form" onSubmit={(event) => void sendLink(event)} noValidate>
          <p className="muted">{signedIn ? words.linkLead : words.linkSignIn}</p>
          <label className="field">
            {words.linkLabel}
            <input type="url" value={link} inputMode="url" disabled={!signedIn} onChange={(event) => setLink(event.target.value)} data-testid="link-input" />
          </label>
          <button type="submit" className="btn" disabled={!signedIn || !link.trim()}>
            <Link2 aria-hidden="true" />
            {words.linkButton}
          </button>
        </form>
      </>
    );
  }

  return (
    <section aria-labelledby="upload-title">
      <h1 id="upload-title" className="screen-title">
        {words.heading}
      </h1>
      <p className="screen-why">{words.why}</p>
      {body}
    </section>
  );
}
