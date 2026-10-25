import { useQueryClient } from "@tanstack/react-query";
import { Pencil } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiError, postJson } from "../api/client";
import { protocolChanged, protocolPath } from "../api/queries";
import type { CatalogEntry } from "../api/types";
import { title as words } from "../copy/ko";
import { shownTitle } from "../lib/protocolTitle";
import { Note } from "./Pieces";

interface Props {
  entry: Pick<CatalogEntry, "protocol_id" | "title" | "source_filename">;
  /** The heading level the name is drawn as (a screen title by default). */
  as?: "h1" | "h2";
  /** Whether [이름 바꾸기] is offered (not for the development fixture). */
  renamable?: boolean;
}

/**
 * The protocol's name as a person reads it (lane RX1, decision 4): the title
 * the analysis read, or the file's name when that title looks like a cover
 * line, with the read title in small print and [이름 바꾸기].
 */
export function TitleLine({ entry, as = "h1", renamable = true }: Props) {
  const client = useQueryClient();
  const shown = shownTitle(entry);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const Heading = as;

  function open() {
    setDraft(shown.text);
    setError(null);
    setEditing(true);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const name = draft.trim();
    if (!name || name.length > 120) {
      setError(words.invalid);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await postJson(protocolPath(entry.protocol_id, "/title"), { title: name });
    } catch (failure) {
      setBusy(false);
      setError(failure instanceof ApiError && failure.code === "protocol_title_invalid" ? words.invalid : words.failed);
      return;
    }
    setBusy(false);
    setEditing(false);
    protocolChanged(client, entry.protocol_id);
  }

  if (editing) {
    return (
      <form className="title-edit" onSubmit={(event) => void save(event)} noValidate data-testid="title-edit">
        <label className="field">
          {words.label}
          <input name="title" type="text" value={draft} maxLength={120} autoFocus onChange={(event) => setDraft(event.target.value)} />
        </label>
        {error ? <Note testId="title-error">{error}</Note> : null}
        <div className="title-edit-actions">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {words.save}
          </button>
          <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => setEditing(false)}>
            {words.cancel}
          </button>
        </div>
      </form>
    );
  }

  return (
    <div className="title-line">
      <Heading className="screen-title" data-testid="protocol-title">
        {shown.text}
      </Heading>
      {shown.fromFile && shown.readTitle ? (
        <p className="muted" data-testid="title-read-as">
          {words.readAs(shown.readTitle)}
        </p>
      ) : null}
      {renamable ? (
        <button type="button" className="btn btn-quiet" onClick={open} data-testid="title-rename">
          <Pencil aria-hidden="true" />
          {words.rename}
        </button>
      ) : null}
    </div>
  );
}
