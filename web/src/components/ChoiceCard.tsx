import { Check } from "lucide-react";
import { useState, type FormEvent } from "react";

import type { CardChoice, CardStep, CardValue, ReviewCard } from "../api/types";
import { review as words } from "../copy/ko";
import { decisionWords, needsOf } from "../lib/reviewCards";
import { Note } from "./Pieces";

export interface Written {
  text?: string;
  minutes?: number;
  memo?: string;
}

interface Props {
  card: ReviewCard;
  busy: boolean;
  note: string | null;
  /** A button was pressed; `value` is the value row's (an OCR page's value) when it has one. */
  onChoose: (choice: CardChoice, written: Written, value?: CardValue) => void;
  onMemo?: (memo: string) => void;
}

function stepWords(step: CardStep): string {
  const label = step.label_shown || step.label || "";
  const printed = step.label_printed === true || (step.label_printed === undefined && Boolean(step.label));
  const text = String(step.text ?? "");
  const shownLabel = label ? (printed ? label : `[${label}]`) : "";
  return shownLabel && !text.startsWith(label) ? `${shownLabel} ${text}` : text;
}

/**
 * One card, drawn from its fields alone (lane RX1, decision 2; the old
 * screen's renderChoiceCard): what it is about, the question, the reasons,
 * the item as read, the change and the proposal, two readings, the steps,
 * the values each with its own buttons, the buttons -- and the words a
 * button needs (a value written, a timer in minutes, a memo). It never
 * looks at the card's type.
 */
export function ChoiceCard({ card, busy, note, onChoose, onMemo }: Props) {
  const [writing, setWriting] = useState<{ choice: CardChoice; value?: CardValue } | null>(null);
  const [memoOpen, setMemoOpen] = useState(false);
  const [text, setText] = useState("");
  const [minutes, setMinutes] = useState("");
  const [memo, setMemo] = useState("");
  const [fieldError, setFieldError] = useState<string | null>(null);

  function press(choice: CardChoice, value?: CardValue) {
    const needs = needsOf(choice);
    if (needs.text || needs.seconds) {
      setWriting({ choice, value });
      setText("");
      setMinutes("");
      setFieldError(null);
      return;
    }
    onChoose(choice, {}, value);
  }

  function sendWritten(event: FormEvent) {
    event.preventDefault();
    if (!writing) return;
    const needs = needsOf(writing.choice);
    if (needs.seconds) {
      const value = Number(minutes);
      if (!Number.isFinite(value) || value <= 0) {
        setFieldError(words.needMinutes);
        return;
      }
      onChoose(writing.choice, { minutes: value }, writing.value);
    } else {
      if (!text.trim()) {
        setFieldError(words.needText);
        return;
      }
      onChoose(writing.choice, { text: text.trim() }, writing.value);
    }
    setWriting(null);
  }

  function sendMemo(event: FormEvent) {
    event.preventDefault();
    if (!memo.trim()) {
      setFieldError(words.needMemo);
      return;
    }
    onMemo?.(memo.trim());
    setMemo("");
    setMemoOpen(false);
  }

  const decided = card.decided === true;
  const decision = decided ? decisionWords(card) : null;

  function choiceButtons(choices: CardChoice[], value?: CardValue) {
    return (
      <div className="card-choices">
        {choices.map((choice) => (
          <button
            key={choice.choice_id}
            type="button"
            className={`btn ${choice.primary ? "btn-primary" : ""}`}
            disabled={busy}
            onClick={() => press(choice, value)}
            data-choice={choice.choice_id}
          >
            {choice.label_ko}
            {choice.engine ? <span className="chip" data-kind="quiet">{choice.engine}</span> : null}
          </button>
        ))}
      </div>
    );
  }

  return (
    <article className="choice-card" data-card-id={card.card_id} data-decided={decided ? "true" : undefined} aria-busy={busy}>
      <p className="choice-card-about">
        {card.step_label ? `${words.stepOf(String(card.step_label))} · ` : ""}
        {card.kind_ko}
      </p>
      <h2 className="choice-card-question">{card.question_ko}</h2>
      {card.reasons_ko?.length ? <p className="muted">{card.reasons_ko.join(" · ")}</p> : null}
      {card.shown ? (
        <p className="choice-card-shown">
          <span className="muted">{words.readAs}</span> <strong>{card.shown}</strong>
        </p>
      ) : null}
      {card.source?.excerpt ? (
        <p className="choice-card-excerpt">
          <span className="muted">{words.excerpt}</span> <q>{card.source.excerpt}</q>
        </p>
      ) : null}
      {card.change ? (
        <p className="choice-card-shown">
          {card.change.from} → {card.change.to}
        </p>
      ) : null}
      {card.proposal && !card.change ? (
        <p className="choice-card-shown">
          <span className="muted">{words.proposal}</span> <strong>{card.proposal}</strong>
        </p>
      ) : null}
      {Array.isArray(card.readings) && card.readings.length ? (
        <ol className="choice-card-readings">
          {card.readings.map((reading, index) => (
            <li key={index}>
              <span className="chip" data-kind="quiet">{index === 0 ? words.readingA : words.readingB}</span> {String(reading.text ?? "")}
            </li>
          ))}
        </ol>
      ) : null}
      {Array.isArray(card.steps) && card.steps.length ? (
        <ol className="choice-card-steps">
          {card.steps.map((step, index) => (
            <li key={index}>{stepWords(step)}</li>
          ))}
        </ol>
      ) : null}
      {Array.isArray(card.values) && card.values.length ? (
        <ul className="choice-card-values">
          {card.values.map((value) => (
            <li key={value.item_id} data-value-id={value.item_id} data-decided={value.decided ? "true" : undefined}>
              <p>
                <strong>{words.valueOf(String(value.shown ?? ""))}</strong>
                {value.reason_ko ? <span className="muted"> · {value.reason_ko}</span> : null}
              </p>
              {value.decided ? (
                <p className="muted">
                  <Check aria-hidden="true" /> {words.decided}
                </p>
              ) : (
                choiceButtons(value.choices, value)
              )}
            </li>
          ))}
        </ul>
      ) : null}
      {decided ? (
        <p className="choice-card-decided" data-testid="card-decided">
          <Check aria-hidden="true" />
          <span>{decision ? words.decidedAs(decision) : words.decided}</span>
        </p>
      ) : null}
      {!decided && writing ? (
        <form className="card-write" onSubmit={sendWritten} noValidate>
          <label className="field">
            {needsOf(writing.choice).seconds ? words.writeMinutes : words.writeText}
            {needsOf(writing.choice).seconds ? (
              <input type="number" inputMode="decimal" min={1} value={minutes} autoFocus onChange={(event) => setMinutes(event.target.value)} />
            ) : (
              <input type="text" value={text} autoFocus onChange={(event) => setText(event.target.value)} />
            )}
          </label>
          {fieldError ? <Note>{fieldError}</Note> : null}
          <div className="card-choices">
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {words.send}
            </button>
            <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => setWriting(null)}>
              {words.cancel}
            </button>
          </div>
        </form>
      ) : null}
      {!decided && !writing && !(card.values?.length && !card.choices.length) ? choiceButtons(card.choices) : null}
      {note ? <Note testId="card-note">{note}</Note> : null}
      {card.memo_allowed && onMemo ? (
        memoOpen ? (
          <form className="card-write" onSubmit={sendMemo} noValidate>
            <label className="field">
              {words.writeMemo}
              <input type="text" value={memo} autoFocus onChange={(event) => setMemo(event.target.value)} />
            </label>
            <div className="card-choices">
              <button type="submit" className="btn" disabled={busy}>
                {words.send}
              </button>
              <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => setMemoOpen(false)}>
                {words.cancel}
              </button>
            </div>
          </form>
        ) : (
          <button type="button" className="btn btn-quiet card-memo" disabled={busy} onClick={() => setMemoOpen(true)}>
            {words.memo}
          </button>
        )
      ) : null}
    </article>
  );
}
