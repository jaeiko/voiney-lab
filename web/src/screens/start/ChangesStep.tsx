import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { ApiError } from "../../api/client";
import { runApi, serverWords, type ChangesChosen } from "../../api/runApi";
import { ErrorBox, LoadingBox, Note } from "../../components/Pieces";
import { start as copy } from "../../copy/ko";

// "오늘 바꿀 값" -- lane EASY's contract (GET/POST /api/protocols/{id}/
// start-check/changes), as the old start sheet uses it. The server lists the
// values the analysis read and checks each new one (the same kind of unit, a
// value of the list); a value under half or over twice the source's is asked
// once more; an empty choice is the step passed. The start names the choice
// it returns (run_change_set_id). Lane RX1 builds the protocol side's part
// for these values; this step keeps the same contract so either can stand
// here (lane RX2 report).

interface Props {
  protocolId: string;
  onDone: (chosen: ChangesChosen) => void;
  footer: (primary: React.ReactNode) => React.ReactNode;
}

export function ChangesStep({ protocolId, onDone, footer }: Props) {
  const list = useQuery({ queryKey: ["protocols", protocolId, "changes"], queryFn: () => runApi.changes(protocolId), retry: false, staleTime: 0 });
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [fromLastTime, setFromLastTime] = useState(false);
  const [large, setLarge] = useState<Array<{ item_id: string; question_ko?: string }> | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const typed = Object.entries(inputs)
    .map(([item_id, value]) => ({ item_id, value: value.trim() }))
    .filter((item) => item.value);

  async function submit(confirmLarge: string[] = []) {
    if (busy || !list.data) return;
    setBusy(true);
    setNote(null);
    const changes = list.data.values.length ? typed : [];
    try {
      const chosen = await runApi.chooseChanges(protocolId, {
        target: list.data.target ?? null,
        changes,
        source: changes.length && fromLastTime ? "last_time" : "wizard",
        ...(confirmLarge.length ? { confirm_large: confirmLarge } : {}),
      });
      setLarge(null);
      onDone(chosen);
    } catch (error) {
      const detail = error instanceof ApiError ? (error.payload as { detail?: { code?: string; large?: Array<{ item_id: string; question_ko?: string }> } } | null)?.detail : null;
      if (detail?.code === "run_change_large_unconfirmed" && Array.isArray(detail.large)) setLarge(detail.large);
      else setNote(serverWords(error) ?? copy.changes.sendFailed);
    } finally {
      setBusy(false);
    }
  }

  if (list.isPending) return <LoadingBox label={copy.changes.loading} />;
  if (list.isError) return <ErrorBox message={copy.changes.failed} onRetry={() => void list.refetch()} />;
  const values = list.data.values;
  if (!values.length) {
    return (
      <>
        <p className="start-lead" data-testid="changes-none">{copy.changes.none}</p>
        {note ? <Note>{note}</Note> : null}
        {footer(
          <button type="button" className="btn btn-primary btn-big" disabled={busy} onClick={() => void submit()} data-testid="start-next">
            {copy.next}
          </button>,
        )}
      </>
    );
  }
  if (large) {
    return (
      <>
        <p className="start-lead">{copy.changes.largeAsk}</p>
        <ul className="start-list" data-testid="changes-large">
          {large.map((item) => (
            <li key={item.item_id}>{item.question_ko ?? ""}</li>
          ))}
        </ul>
        {note ? <Note>{note}</Note> : null}
        {footer(
          <>
            <button type="button" className="btn btn-big" disabled={busy} onClick={() => setLarge(null)}>
              {copy.changes.largeFix}
            </button>
            <button type="button" className="btn btn-primary btn-big" disabled={busy} onClick={() => void submit(large.map((item) => item.item_id))} data-testid="changes-large-yes">
              {copy.changes.largeYes}
            </button>
          </>,
        )}
      </>
    );
  }
  const last = list.data.last_time?.changes ?? [];
  return (
    <>
      <p className="start-lead">{copy.changes.lead}</p>
      {last.length ? (
        <div className="start-last-time">
          <button
            type="button"
            className="btn"
            onClick={() => {
              setInputs(Object.fromEntries(last.map((item) => [item.item_id, item.shown_ko])));
              setFromLastTime(true);
            }}
          >
            {copy.changes.lastTime(last.length)}
          </button>
          <p className="muted">
            {copy.changes.lastTimeLine(list.data.last_time?.when_ko || "", last.map((item) => `${item.step_label}단계 ${item.kind_ko} ${item.shown_ko}`).join(" · "))}
          </p>
        </div>
      ) : null}
      <ul className="start-changes" data-testid="changes-list">
        {values.map((value) => (
          <li key={value.item_id}>
            <label className="field">
              <span>{copy.changes.field(value.step_label, value.kind_ko, value.shown_ko)}</span>
              <input
                type="text"
                autoComplete="off"
                inputMode="decimal"
                placeholder={copy.changes.placeholder(value.units_ko)}
                value={inputs[value.item_id] ?? ""}
                onChange={(event) => {
                  setInputs({ ...inputs, [value.item_id]: event.target.value });
                  setFromLastTime(false);
                }}
              />
            </label>
          </li>
        ))}
      </ul>
      {note ? <Note>{note}</Note> : null}
      {footer(
        <button type="button" className="btn btn-primary btn-big" disabled={busy} onClick={() => void submit()} data-testid="start-next">
          {typed.length ? copy.changes.use(typed.length) : copy.changes.keep}
        </button>,
      )}
    </>
  );
}
