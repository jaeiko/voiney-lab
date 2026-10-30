import { useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, History, Minus } from "lucide-react";
import { useState, type FormEvent } from "react";

import { ApiError, postJson } from "../api/client";
import { protocolPath, useRunChanges } from "../api/queries";
import type { ChangeableValue, RunChange, RunChangeOptions, RunChangesChosen, RunStep } from "../api/types";
import { changes as words } from "../copy/ko";
import { chosenFromLastTime, numberLooksRight, sentenceSegments, unitChoices, valueText, type ChosenValue } from "../lib/runChanges";
import { ErrorBox, LoadingBox, Note } from "./Pieces";

interface Props {
  protocolId: string;
  /** The person's choice is recorded (the change set the start names), with the values chosen (none for 원문대로). */
  onChosen: (chosen: RunChangesChosen) => void;
}

type View = "ask" | "steps" | "step" | "value" | "large";

interface LargeAsk {
  questions: string[];
  itemIds: string[];
}

function stepsOf(options: RunChangeOptions): RunStep[] {
  if (Array.isArray(options.steps) && options.steps.length) return options.steps;
  // An older server without the steps list: one step per value's step, the value's words as the sentence.
  const seen = new Map<string, RunStep>();
  for (const value of options.values) {
    const step = seen.get(value.step_id) ?? { step_id: value.step_id, step_label: value.step_label, text: "", text_ko: null, value_ids: [] };
    step.value_ids.push(value.item_id);
    seen.set(value.step_id, step);
  }
  return [...seen.values()];
}

/**
 * "오늘 원문과 다르게 할 값" (lane RX1, decision 3; the start flow of lane
 * RX2 uses this piece): one big button for the usual case -- nothing to
 * change -- and a path for the other: the steps, a step's sentence (Korean
 * first, the source folded) with its changeable values marked, a value's
 * number and unit of the same family, the comparison with the source, the
 * list chosen and [다 골랐어요]. The server checks every value again and
 * asks once more about a large one; the screen only carries its answer.
 */
export function RunChangesStep({ protocolId, onChosen }: Props) {
  const client = useQueryClient();
  const query = useRunChanges(protocolId);
  const [view, setView] = useState<View>("ask");
  const [stepId, setStepId] = useState<string | null>(null);
  const [valueId, setValueId] = useState<string | null>(null);
  const [chosen, setChosen] = useState<ChosenValue[]>([]);
  const [source, setSource] = useState<"wizard" | "last_time" | "lab_revision">("wizard");
  const [saveLabRevision, setSaveLabRevision] = useState(false);
  const [number, setNumber] = useState("");
  const [unit, setUnit] = useState("");
  const [asText, setAsText] = useState(false);
  const [freeText, setFreeText] = useState("");
  const [showSource, setShowSource] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [large, setLarge] = useState<LargeAsk | null>(null);

  if (query.isPending) return <LoadingBox label={words.loading} />;
  if (query.isError) {
    const message = query.error instanceof ApiError && query.error.messageKo ? query.error.messageKo : words.loadFailed;
    return <ErrorBox message={message} onRetry={() => void query.refetch()} />;
  }
  const options = query.data;
  const values = new Map(options.values.map((value) => [value.item_id, value]));
  const steps = stepsOf(options);
  const step = steps.find((item) => item.step_id === stepId) ?? null;
  const value = valueId ? (values.get(valueId) ?? null) : null;
  const lastTime = options.last_time?.changes?.length ? options.last_time : null;
  const labRevision = options.lab_revision?.changes?.length ? options.lab_revision : null;

  async function send(items: ChosenValue[], from: "wizard" | "last_time" | "lab_revision", confirmLarge: string[] = []) {
    setBusy(true);
    setNote(null);
    try {
      const answer = await postJson<RunChangesChosen>(protocolPath(protocolId, "/start-check/changes"), {
        target: options.target,
        source: from,
        changes: items.map((item) => ({ item_id: item.item_id, value: item.text })),
        ...(saveLabRevision && items.length ? { save_as_lab_revision: true } : {}),
        ...(confirmLarge.length ? { confirm_large: confirmLarge } : {}),
      });
      setBusy(false);
      void client.invalidateQueries({ queryKey: ["protocols", protocolId] });
      onChosen(answer);
    } catch (failure) {
      setBusy(false);
      if (failure instanceof ApiError && failure.code === "run_change_large_unconfirmed") {
        const rows = Array.isArray(failure.detail?.large) ? (failure.detail!.large as RunChange[]) : [];
        setLarge({
          questions: rows.map((row) => row.question_ko).filter((row): row is string => typeof row === "string" && Boolean(row)),
          itemIds: rows.map((row) => row.item_id),
        });
        if (!rows.length && failure.messageKo) setLarge({ questions: [failure.messageKo], itemIds: items.map((item) => item.item_id) });
        setView("large");
        return;
      }
      setNote(failure instanceof ApiError && failure.messageKo ? failure.messageKo : words.loadFailed);
    }
  }

  function openValue(next: ChangeableValue) {
    const already = chosen.find((item) => item.item_id === next.item_id);
    setValueId(next.item_id);
    // A value chosen before opens as it was typed (its own number and unit); words written freely open as words.
    setNumber(already?.number ?? "");
    setUnit(already?.unit ?? next.unit);
    setAsText(Boolean(already && already.number === undefined));
    setFreeText(already && already.number === undefined ? already.text : "");
    setNote(null);
    setView("value");
  }

  function useValue(event: FormEvent) {
    event.preventDefault();
    if (!value) return;
    let text: string;
    if (asText) {
      text = freeText.trim();
      if (!text) {
        setNote(words.needNumber);
        return;
      }
    } else {
      if (!numberLooksRight(number)) {
        setNote(words.needNumber);
        return;
      }
      text = valueText(number, unit);
    }
    if (chosen.length >= options.limit && !chosen.some((item) => item.item_id === value.item_id)) {
      setNote(words.limit(options.limit));
      return;
    }
    const next: ChosenValue = asText
      ? { item_id: value.item_id, value, text, shown: text }
      : { item_id: value.item_id, value, text, shown: text, number: number.trim(), unit };
    setChosen((current) => [...current.filter((item) => item.item_id !== value.item_id), next]);
    setSource("wizard");
    setNote(null);
    setView("step");
  }

  function remove(itemId: string) {
    setChosen((current) => current.filter((item) => item.item_id !== itemId));
  }

  function fromLastTime() {
    if (!lastTime) return;
    setChosen(chosenFromLastTime(options.values, lastTime.changes));
    setSource("last_time");
    setView("steps");
  }

  function fromLabRevision() {
    if (!labRevision) return;
    setChosen(chosenFromLastTime(options.values, labRevision.changes));
    setSource("lab_revision");
    setSaveLabRevision(false);
    setView("steps");
  }

  const chosenList = (
    <section className="chosen-list" aria-label={words.chosenHeading(chosen.length)}>
      {source === "last_time" ? <p className="muted">{words.loadedFromLastTime(lastTime?.when_ko ?? "")}</p> : null}
      {source === "lab_revision" && labRevision ? <p className="muted">{words.loadedFromLabRevision(labRevision.label_ko)}</p> : null}
      <h3>{words.chosenHeading(chosen.length)}</h3>
      {chosen.length ? (
        <ul>
          {chosen.map((item) => (
            <li key={item.item_id} data-chosen={item.item_id}>
              <span>
                {words.stepOf(item.value.step_label)} {item.value.kind_ko} · {words.compare(item.value.shown_ko, item.shown)}
              </span>
              <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => remove(item.item_id)}>
                <Minus aria-hidden="true" />
                {words.remove}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{words.chosenEmpty}</p>
      )}
      {chosen.length ? (
        <label className="check-row">
          <input
            type="checkbox"
            checked={saveLabRevision}
            onChange={(event) => setSaveLabRevision(event.target.checked)}
          />
          <span>{words.saveLabRevision}</span>
        </label>
      ) : null}
      <div className="card-choices">
        <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void send(chosen, source)} data-testid="changes-done">
          {busy ? words.sending : words.done}
        </button>
      </div>
    </section>
  );

  if (view === "ask") {
    return (
      <div className="changes-ask" data-view="ask">
        <button type="button" className="big-card big-card-button" data-card="none" disabled={busy} onClick={() => void send([], "wizard")} data-testid="changes-none">
          <span className="big-card-title">
            <span>{busy ? words.sending : words.none}</span>
          </span>
        </button>
        <div className="secondary-actions">
          <button type="button" className="btn" disabled={busy} onClick={() => setView("steps")} data-testid="changes-some">
            {words.some}
          </button>
          {lastTime ? (
            <button type="button" className="btn" disabled={busy} onClick={fromLastTime} data-testid="changes-last-time">
              <History aria-hidden="true" />
              {words.lastTime(lastTime.changes.length, lastTime.when_ko ?? "")}
            </button>
          ) : null}
          {labRevision ? (
            <button type="button" className="btn" disabled={busy} onClick={fromLabRevision} data-testid="changes-lab-revision">
              <History aria-hidden="true" />
              {words.labRevision(labRevision.label_ko, labRevision.changes.length)}
            </button>
          ) : null}
        </div>
        {note ? <Note>{note}</Note> : null}
      </div>
    );
  }

  if (view === "large" && large) {
    return (
      <div className="changes-large" data-view="large">
        <h2 className="choice-card-question">{words.largeHeading}</h2>
        {large.questions.map((question, index) => (
          <p key={index} data-testid="large-question">
            {question}
          </p>
        ))}
        <div className="card-choices">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void send(chosen, source, large.itemIds)} data-testid="large-confirm">
            {words.largeConfirm}
          </button>
          <button
            type="button"
            className="btn btn-quiet"
            disabled={busy}
            onClick={() => {
              setLarge(null);
              setView("steps");
            }}
          >
            {words.largeFix}
          </button>
        </div>
        {note ? <Note>{note}</Note> : null}
      </div>
    );
  }

  if (view === "value" && value && step) {
    const units = unitChoices(value.units_ko, value.unit);
    const today = asText ? freeText.trim() : numberLooksRight(number) ? valueText(number, unit) : "";
    return (
      <form className="value-editor" data-view="value" onSubmit={useValue} noValidate>
        <button type="button" className="btn btn-quiet" onClick={() => setView("step")}>
          <ChevronLeft aria-hidden="true" />
          {words.stepOf(step.step_label)}
        </button>
        <h2 className="choice-card-question">
          {words.stepOf(value.step_label)} {value.kind_ko} · {value.shown_ko}
        </h2>
        {!asText ? (
          <>
            <label className="field">
              {words.number}
              <input type="text" inputMode="decimal" value={number} autoFocus onChange={(event) => setNumber(event.target.value)} data-testid="value-number" />
            </label>
            <div className="unit-choices" role="group" aria-label={words.unit}>
              {units.map((choice) => (
                <button
                  key={choice.unit}
                  type="button"
                  className="btn"
                  aria-pressed={unit === choice.unit}
                  onClick={() => setUnit(choice.unit)}
                  data-unit={choice.unit}
                >
                  {choice.label}
                </button>
              ))}
            </div>
          </>
        ) : (
          <label className="field">
            {words.textLabel}
            <input type="text" value={freeText} autoFocus onChange={(event) => setFreeText(event.target.value)} data-testid="value-text" />
            <span className="muted">{words.textHint}</span>
          </label>
        )}
        <p className="value-compare" data-testid="value-compare">
          {today ? words.compare(value.shown_ko, today) : words.compare(value.shown_ko, "—")}
        </p>
        {note ? <Note>{note}</Note> : null}
        <div className="card-choices">
          <button type="submit" className="btn btn-primary" data-testid="value-use">
            {words.use}
          </button>
          <button type="button" className="btn btn-quiet" onClick={() => setView("step")}>
            {words.cancel}
          </button>
          <button type="button" className="btn btn-quiet" onClick={() => setAsText((current) => !current)}>
            {asText ? words.asNumber : words.asText}
          </button>
        </div>
      </form>
    );
  }

  if (view === "step" && step) {
    const stepValues = step.value_ids.map((id) => values.get(id)).filter((item): item is ChangeableValue => Boolean(item));
    const segments = sentenceSegments(step.text, stepValues);
    const korean = step.text_ko;
    return (
      <div className="step-sentence" data-view="step">
        <button type="button" className="btn btn-quiet" onClick={() => setView("steps")}>
          <ChevronLeft aria-hidden="true" />
          {words.backToSteps}
        </button>
        <h2 className="choice-card-question">{words.stepOf(step.step_label)}</h2>
        {korean ? <p className="step-korean" data-testid="step-korean">{korean}</p> : null}
        {korean ? (
          <button type="button" className="btn btn-quiet" onClick={() => setShowSource((current) => !current)} data-testid="step-source-toggle">
            {showSource ? words.sourceHide : words.sourceShow}
          </button>
        ) : null}
        {!korean || showSource ? (
          <p className="step-source" data-testid="step-source">
            {segments.map((segment, index) =>
              segment.value ? (
                <button key={index} type="button" className="value-mark" onClick={() => openValue(segment.value as ChangeableValue)} data-value={segment.value.item_id}>
                  {segment.text}
                </button>
              ) : (
                <span key={index}>{segment.text}</span>
              ),
            )}
          </p>
        ) : null}
        <p className="muted">{stepValues.length ? words.valuesLead : words.stepNoValues}</p>
        {note ? <Note>{note}</Note> : null}
        {stepValues.length ? (
          <div className="card-choices" data-testid="step-values">
            {stepValues.map((item) => (
              <button key={item.item_id} type="button" className="btn" onClick={() => openValue(item)} data-value={item.item_id} aria-pressed={chosen.some((entry) => entry.item_id === item.item_id)}>
                {item.kind_ko} {item.shown_ko}
              </button>
            ))}
          </div>
        ) : null}
        {chosenList}
      </div>
    );
  }

  // The steps.
  return (
    <div className="changes-steps" data-view="steps">
      <button type="button" className="btn btn-quiet" onClick={() => setView("ask")}>
        <ChevronLeft aria-hidden="true" />
        {words.cancel}
      </button>
      {options.values.length ? <p className="muted">{words.stepsLead}</p> : <p className="muted">{words.noValues}</p>}
      <ul className="row-list" aria-label={words.heading}>
        {steps.map((item) => (
          <li key={item.step_id} className="row" data-step={item.step_id}>
            <div className="row-head">
              <span className="row-title">{words.stepOf(item.step_label)}</span>
              <span className="chip" data-kind={item.value_ids.length ? "ready" : "quiet"}>
                {item.value_ids.length ? words.stepValues(item.value_ids.length) : words.stepNoValues}
              </span>
            </div>
            {item.text_ko || item.text ? <p className="row-meta">{item.text_ko ?? item.text}</p> : null}
            {item.value_ids.length ? (
              <div className="row-action">
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    setStepId(item.step_id);
                    setShowSource(false);
                    setView("step");
                  }}
                >
                  {words.stepOf(item.step_label)}
                </button>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
      {lastTime ? (
        <div className="secondary-actions">
          <button type="button" className="btn" disabled={busy} onClick={fromLastTime} data-testid="changes-last-time">
            <History aria-hidden="true" />
            {words.lastTime(lastTime.changes.length, lastTime.when_ko ?? "")}
          </button>
        </div>
      ) : null}
      {labRevision ? (
        <div className="secondary-actions">
          <button type="button" className="btn" disabled={busy} onClick={fromLabRevision} data-testid="changes-lab-revision">
            <History aria-hidden="true" />
            {words.labRevision(labRevision.label_ko, labRevision.changes.length)}
          </button>
        </div>
      ) : null}
      {note ? <Note>{note}</Note> : null}
      {chosenList}
    </div>
  );
}
