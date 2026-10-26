import * as Dialog from "@radix-ui/react-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, ChevronDown, ChevronUp, Mic, NotebookPen, Paperclip } from "lucide-react";
import { useRef, useState } from "react";

import { runApi, serverWords, type TimelineEvent } from "../../api/runApi";
import { Note } from "../../components/Pieces";
import { run as copy, frame } from "../../copy/ko";

// The run's records beside the step (person's decision 2): what was recorded
// by voice, and a memo or a photo added by hand, with the two add buttons
// always on the screen -- a column of its own on a wide screen, a panel at
// the bottom that folds on a narrow one (its head and buttons stay). The list
// is the server's timeline of the saved experiment; a memo and a photo go to
// the same routes the old screen's records use and change nothing in the run.

const PHOTO_MAX_BYTES = 32 * 1024 * 1024;

export interface RecordLine {
  key: string;
  kind: "voice" | "memo" | "photo";
  step: string | null;
  text: string;
  state: "corrected" | "retracted" | null;
}

/** The timeline's records, newest first: observations (by voice or typed) and attached files. */
export function recordLines(timeline: TimelineEvent[] | undefined): RecordLine[] {
  const lines: RecordLine[] = [];
  (timeline ?? []).forEach((event, at) => {
    if (event.event_type === "observation_recorded" && event.observation && typeof event.observation.content === "string") {
      const observation = event.observation;
      lines.push({
        key: event.event_id ?? `o${at}`,
        kind: observation.capture_source === "voice" ? "voice" : "memo",
        step: event.step_label ?? null,
        text: observation.retracted ? observation.content : observation.current_content ?? observation.content,
        state: observation.retracted ? "retracted" : observation.corrected ? "corrected" : null,
      });
    } else if (event.event_type === "evidence_attached" && event.evidence) {
      lines.push({ key: event.event_id ?? `e${at}`, kind: "photo", step: event.step_label ?? null, text: event.evidence.original_filename ?? copy.records.photoKind, state: null });
    }
  });
  return lines.reverse();
}

const KIND_ICON = { voice: Mic, memo: NotebookPen, photo: Paperclip } as const;
const KIND_WORD = { voice: copy.records.voice, memo: copy.records.memoKind, photo: copy.records.photoKind } as const;

export function RecordsPanel({ experimentId, refreshKey, narrow }: { experimentId: string | null; refreshKey: string; narrow: boolean }) {
  const client = useQueryClient();
  const timeline = useQuery({
    queryKey: ["workspace", "experiments", experimentId ?? "", "timeline", refreshKey],
    queryFn: () => runApi.timeline(experimentId ?? ""),
    enabled: Boolean(experimentId),
    retry: false,
    refetchInterval: 15000,
  });
  const [open, setOpen] = useState(false);
  const [memoOpen, setMemoOpen] = useState(false);
  const [memo, setMemo] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; failed: boolean } | null>(null);
  const photo = useRef<HTMLInputElement>(null);
  const lines = recordLines(timeline.data?.timeline);
  const canAdd = Boolean(experimentId);

  async function refresh() {
    await client.invalidateQueries({ queryKey: ["workspace", "experiments", experimentId ?? "", "timeline"] });
  }

  async function saveMemo() {
    const content = memo.trim();
    if (!experimentId || !content || busy) return;
    setBusy(true);
    try {
      await runApi.addMemo(experimentId, content);
      setMemo("");
      setMemoOpen(false);
      setNote({ text: copy.records.memoSaved, failed: false });
      await refresh();
    } catch (error) {
      setNote({ text: serverWords(error) ?? copy.records.failed, failed: true });
    } finally {
      setBusy(false);
    }
  }

  async function addPhoto(file: File | undefined) {
    if (!experimentId || !file || busy) return;
    if (file.size < 1 || file.size > PHOTO_MAX_BYTES) {
      setNote({ text: copy.records.tooBig, failed: true });
      return;
    }
    setBusy(true);
    try {
      await runApi.addPhoto(experimentId, file);
      setNote({ text: copy.records.photoSaved, failed: false });
      await refresh();
    } catch (error) {
      setNote({ text: serverWords(error) ?? copy.records.failed, failed: true });
    } finally {
      setBusy(false);
      if (photo.current) photo.current.value = "";
    }
  }

  const listShown = !narrow || open;
  return (
    <aside className="run-records" data-open={listShown} aria-labelledby="run-records-title" data-testid="run-records">
      <div className="run-records-head">
        <h2 id="run-records-title">
          {copy.records.title}
          {lines.length ? <span className="run-records-count"> · {copy.records.count(lines.length)}</span> : null}
        </h2>
        <div className="run-records-actions">
          <button type="button" className="btn run-btn" disabled={!canAdd || busy} onClick={() => setMemoOpen(true)} data-testid="record-memo">
            <NotebookPen aria-hidden="true" />
            <span>{copy.records.memo}</span>
          </button>
          <button type="button" className="btn run-btn" disabled={!canAdd || busy} onClick={() => photo.current?.click()} data-testid="record-photo">
            <Camera aria-hidden="true" />
            <span>{copy.records.photo}</span>
          </button>
          <input ref={photo} type="file" accept="image/*" capture="environment" hidden onChange={(event) => void addPhoto(event.target.files?.[0])} data-testid="record-photo-input" />
          {narrow ? (
            <button type="button" className="btn btn-quiet run-btn" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="records-toggle">
              {open ? <ChevronDown aria-hidden="true" /> : <ChevronUp aria-hidden="true" />}
              <span className="sr-only">{open ? copy.records.hide : copy.records.show}</span>
            </button>
          ) : null}
        </div>
      </div>
      {note ? (
        note.failed ? <Note>{note.text}</Note> : <p className="run-records-note" role="status">{note.text}</p>
      ) : null}
      {!listShown && lines.length ? (
        <p className="run-records-latest" data-testid="records-latest">
          <span className="run-record-kind">{KIND_WORD[lines[0].kind]}</span>
          <span className="run-record-text">{lines[0].text}</span>
        </p>
      ) : null}
      {listShown ? (
        <div className="run-records-body">
          {!canAdd ? (
            <p className="muted">{copy.records.unavailable}</p>
          ) : timeline.isError ? (
            <p className="muted">{copy.records.loadFailed}</p>
          ) : lines.length ? (
            <ol className="run-records-list" data-testid="records-list">
              {lines.map((line) => {
                const Icon = KIND_ICON[line.kind];
                return (
                  <li key={line.key} data-kind={line.kind} data-state={line.state ?? undefined}>
                    <span className="run-record-kind">
                      <Icon aria-hidden="true" />
                      <span>{KIND_WORD[line.kind]}</span>
                      {line.step ? <span className="muted"> · {copy.records.step(line.step)}</span> : null}
                      {line.state ? <span className="muted"> · {line.state === "corrected" ? copy.records.corrected : copy.records.retracted}</span> : null}
                    </span>
                    <span className="run-record-text">{line.text}</span>
                  </li>
                );
              })}
            </ol>
          ) : (
            <p className="muted">{copy.records.empty}</p>
          )}
        </div>
      ) : null}
      <Dialog.Root open={memoOpen} onOpenChange={setMemoOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-backdrop" />
          <Dialog.Content className="dialog run-dialog" aria-describedby={undefined}>
            <Dialog.Title asChild>
              <h2>{copy.records.memoTitle}</h2>
            </Dialog.Title>
            <label className="field">
              <span>{copy.records.memoField}</span>
              <textarea rows={4} value={memo} onChange={(event) => setMemo(event.target.value)} data-testid="memo-input" />
            </label>
            <div className="dialog-actions">
              <Dialog.Close asChild>
                <button type="button" className="btn btn-quiet run-btn" disabled={busy}>
                  {frame.cancel}
                </button>
              </Dialog.Close>
              <button type="button" className="btn btn-primary run-btn" disabled={busy || !memo.trim()} onClick={() => void saveMemo()} data-testid="memo-save">
                {busy ? copy.records.saving : copy.records.memoSave}
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </aside>
  );
}
