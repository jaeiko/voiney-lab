import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, CircleAlert, Play, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { useProtocols } from "../api/queries";
import { runApi, serverWords, type StartAnswer, type StartCheck } from "../api/runApi";
import type { CatalogEntry } from "../api/types";
import { Chip, EmptyBox, ErrorBox, LoadingBox, Note } from "../components/Pieces";
import { StepProgress } from "../components/StepProgress";
import { start as copy } from "../copy/ko";
import { go, routes } from "../lib/navigation";
import { protocolStatus, sortEntries } from "../lib/protocolStatus";
import { loadVoiceScripts, primeStart, startRun } from "../voice/engine";
import { RunChangesStep } from "../components/RunChangesStep";
import type { RunChangesChosen } from "../api/types";

// The start flow (lane RX2, person's decision 1 of 2026-10-25): one thing per
// screen, each saying first why it is there, a progress line ① to ⑤ and a
// way back. No voice calibration here (decision 1). The last press keeps the
// one name AGENTS.md rule 5 fixes; the server decides whether the start goes
// (the key-value check answered, the safety notices read, today's values
// chosen) and the experiment screen says it if it does not.

const STEP_COUNT = 5;

function StepFrame({ index, title, children }: { index: number; title: string; children: ReactNode }) {
  return (
    <section className="start-step" data-step={index + 1} aria-labelledby="start-step-title">
      <p className="start-why" data-testid="start-why">{copy.why[index]}</p>
      <h1 className="screen-title" id="start-step-title">
        <span className="sr-only">{copy.stepOf(index + 1, STEP_COUNT)} · </span>
        {title}
      </h1>
      {children}
    </section>
  );
}

function Footer({ onBack, children }: { onBack: () => void; children?: ReactNode }) {
  return (
    <div className="start-footer">
      <button type="button" className="btn btn-quiet btn-big" onClick={onBack} data-testid="start-back">
        <ArrowLeft aria-hidden="true" />
        <span>{copy.back}</span>
      </button>
      <div className="start-footer-primary">{children}</div>
    </div>
  );
}

function StatusChip({ entry }: { entry: CatalogEntry }) {
  const chip = protocolStatus(entry).chips[0];
  return chip ? <Chip chip={chip} /> : null;
}

// ① 프로토콜 확인 ------------------------------------------------------------------
function ProtocolStep({ chosen, onChoose, onNext, onBack }: { chosen: string | null; onChoose: (id: string) => void; onNext: () => void; onBack: () => void }) {
  const protocols = useProtocols();
  const [listing, setListing] = useState(false);
  if (protocols.isPending) return <LoadingBox />;
  if (protocols.isError) return <ErrorBox message={copy.protocol.none} onRetry={() => void protocols.refetch()} />;
  const entries = sortEntries(protocols.data);
  const entry = chosen ? entries.find((item) => item.protocol_id === chosen) ?? null : null;
  const ready = entries.filter((item) => item.available_for_execution);
  if (!ready.length && !entry) {
    return (
      <>
        <EmptyBox message={copy.protocol.none} action={<a className="btn" href={routes.protocols}>{copy.protocol.noneAction}</a>} />
        <Footer onBack={onBack} />
      </>
    );
  }
  const showList = listing || !entry;
  return (
    <>
      {entry && !showList ? (
        <div className="start-protocol" data-testid="start-protocol">
          <p className="start-protocol-title">{entry.title}</p>
          <p className="muted">{copy.protocol.meta(entry.source_filename, entry.step_count ?? null)}</p>
          <StatusChip entry={entry} />
          {!entry.available_for_execution ? <Note>{copy.protocol.notReady}</Note> : null}
          <button type="button" className="btn btn-quiet" onClick={() => setListing(true)}>
            {copy.protocol.choose}
          </button>
        </div>
      ) : (
        <ul className="start-options" data-testid="start-protocol-list">
          {entries.map((item) => (
            <li key={item.protocol_id}>
              <button
                type="button"
                className="start-option"
                aria-pressed={item.protocol_id === chosen}
                disabled={!item.available_for_execution}
                onClick={() => {
                  onChoose(item.protocol_id);
                  setListing(false);
                }}
              >
                <span className="start-option-title">{item.title}</span>
                <StatusChip entry={item} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {chosen && !entry ? <Note>{copy.protocol.notFound}</Note> : null}
      <Footer onBack={onBack}>
        <button type="button" className="btn btn-primary btn-big" disabled={!entry?.available_for_execution} onClick={onNext} data-testid="start-next">
          <span>{copy.protocol.pick}</span>
          <ArrowRight aria-hidden="true" />
        </button>
      </Footer>
    </>
  );
}

// ② 원문 값 확인 ----------------------------------------------------------------------
function CheckStep({ protocolId, onLoaded, onNext, onBack }: { protocolId: string; onLoaded: (check: StartCheck) => void; onNext: () => void; onBack: () => void }) {
  const check = useQuery({ queryKey: ["protocols", protocolId, "start-check"], queryFn: () => runApi.startCheck(protocolId), retry: false, staleTime: 0, gcTime: 0 });
  const [index, setIndex] = useState(0);
  const [wrong, setWrong] = useState<StartAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    if (check.data) onLoaded(check.data);
  }, [check.data, onLoaded]);

  if (check.isPending) return <LoadingBox label={copy.check.loading} />;
  if (check.isError)
    return (
      <>
        <ErrorBox message={serverWords(check.error) ?? copy.check.failed} onRetry={() => void check.refetch()} />
        <Footer onBack={onBack} />
      </>
    );
  const questions = Array.isArray(check.data.questions) ? check.data.questions : [];
  if (!questions.length) {
    return (
      <>
        <EmptyBox message={check.data.enabled === false && check.data.skipped_reason === "disabled" ? copy.check.off : copy.check.none} />
        <Footer onBack={onBack}>
          <button type="button" className="btn btn-primary btn-big" onClick={onNext} data-testid="start-next">
            <span>{copy.next}</span>
            <ArrowRight aria-hidden="true" />
          </button>
        </Footer>
      </>
    );
  }
  const question = questions[Math.min(index, questions.length - 1)];

  async function answer(request: Record<string, unknown>) {
    if (busy) return;
    setBusy(true);
    setNote(null);
    try {
      const result = await runApi.answer(protocolId, request);
      if (result?.proceed === true) {
        setWrong(null);
        if (index + 1 < questions.length) setIndex(index + 1);
        else onNext();
      } else {
        setWrong(result ?? {});
      }
    } catch (error) {
      setNote(serverWords(error) ?? copy.check.sendFailed);
    } finally {
      setBusy(false);
    }
  }

  const lines = question.source?.lines;
  return (
    <>
      <p className="start-count" data-testid="check-progress">{copy.check.progress(Math.min(index, questions.length - 1) + 1, questions.length)}</p>
      <p className="start-question" data-testid="check-question">{question.question_ko || copy.check.lead}</p>
      <blockquote className="start-excerpt">
        <span className="start-excerpt-where">{copy.check.where(question.source?.page ?? null, lines?.first_line ?? null, lines?.last_line ?? null)}</span>
        <span>{question.source?.excerpt ?? ""}</span>
      </blockquote>
      {wrong ? (
        <>
          <Note>{wrong.message_ko || copy.check.again}</Note>
          <div className="start-choices">
            {(wrong.followups ?? []).map((item) =>
              item.choice_id === "open_card" ? (
                <a key={item.choice_id} className="btn btn-big" href={routes.review(protocolId)}>
                  {copy.check.openCard}
                </a>
              ) : item.request ? (
                <button key={item.choice_id} type="button" className="btn btn-big" disabled={busy} onClick={() => void answer(item.request!)}>
                  {item.label_ko}
                </button>
              ) : null,
            )}
          </div>
        </>
      ) : (
        <div className="start-choices" data-testid="check-choices">
          {(question.choices ?? []).map((choice) => (
            <button key={choice.choice_id} type="button" className="btn btn-big" disabled={busy} onClick={() => void answer({ question_id: question.question_id, choice_id: choice.choice_id })}>
              {choice.label_ko}
            </button>
          ))}
        </div>
      )}
      {note ? <Note>{note}</Note> : null}
      <Footer onBack={onBack} />
    </>
  );
}

// ③ 안전 주의 읽기 --------------------------------------------------------------------
function SafetyStep({ protocolId, check, onNext, onBack }: { protocolId: string; check: StartCheck | null; onNext: () => void; onBack: () => void }) {
  const review = useQuery({ queryKey: ["protocols", protocolId, "review"], queryFn: () => runApi.review(protocolId), retry: false });
  // Opened at this step after a refused start: the check's target is read again, since [읽었어요] leaves it.
  const loaded = useQuery({ queryKey: ["protocols", protocolId, "start-check"], queryFn: () => runApi.startCheck(protocolId), retry: false, enabled: !check });
  const target = check ?? loaded.data ?? null;
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (review.isPending || (!check && loaded.isPending)) return <LoadingBox label={copy.safety.loading} />;
  if (review.isError)
    return (
      <>
        <ErrorBox message={copy.safety.failed} onRetry={() => void review.refetch()} />
        <Footer onBack={onBack} />
      </>
    );
  const safety = review.data.safety_notices ?? [];
  const notices = review.data.execution_notices ?? [];
  const scope = review.data.safety_scope?.notice?.trim();

  async function read() {
    if (busy) return;
    if (!target || target.enabled !== true || !target.target || typeof target.target !== "object") {
      onNext();
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      const result = await runApi.safetyRead(protocolId, target.target);
      if (result?.ready === false) setNote(copy.safety.sendFailed);
      else onNext();
    } catch (error) {
      setNote(serverWords(error) ?? copy.safety.sendFailed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {scope ? <p className="start-lead">{scope}</p> : null}
      {safety.length ? (
        <>
          <p className="start-count">{copy.safety.count(safety.length)}</p>
          <ul className="start-safety" data-testid="safety-list">
            {safety.map((item, at) => (
              <li key={at} className="safety-card">
                <p className="safety-card-head">
                  <TriangleAlert aria-hidden="true" />
                  <span>{copy.safety.word}</span>
                  <span className="muted">{copy.safety.where(item.step_label ?? null, item.source_page_number ?? null)}</span>
                </p>
                <p className="safety-card-text">{item.primary_text?.trim() ? item.primary_text : copy.safety.noKorean}</p>
                {item.source_text ? (
                  <details className="source-fold">
                    <summary>{copy.safety.source}</summary>
                    <p lang="en">{item.source_text}</p>
                  </details>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <EmptyBox message={copy.safety.none} />
      )}
      {notices.length ? (
        <div className="start-notices">
          <p className="start-count">
            <CircleAlert aria-hidden="true" />
            <span>{copy.safety.before}</span>
          </p>
          <ul className="start-list">
            {notices.map((item, at) => (
              <li key={at}>{item.message_ko || item.message || ""}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {note ? <Note>{note}</Note> : null}
      <Footer onBack={onBack}>
        <button type="button" className="btn btn-primary btn-big" disabled={busy} onClick={() => void read()} data-testid="start-next">
          <Check aria-hidden="true" />
          <span>{copy.safety.read}</span>
        </button>
      </Footer>
    </>
  );
}

// ⑤ 시작 ---------------------------------------------------------------------------
function AmbientChoice({ value, onChange, disabled }: { value: string; onChange: (value: string) => void; disabled: boolean }) {
  return (
    <fieldset className="ambient" data-testid="ambient-choice">
      <legend>{copy.go.ambient}</legend>
      <div className="segmented">
        {(["quiet", "noisy"] as const).map((option) => (
          <label key={option} className="segmented-option" data-checked={value === option}>
            <input type="radio" name="ambient" value={option} checked={value === option} disabled={disabled} onChange={() => onChange(option)} />
            <span>{option === "quiet" ? `${copy.go.quiet}(${copy.go.quietDefault})` : copy.go.noisy}</span>
          </label>
        ))}
      </div>
      <p className="muted">{copy.go.ambientWhy}</p>
    </fieldset>
  );
}

function GoStep({
  entry,
  chosen,
  resume,
  onBack,
}: {
  entry: CatalogEntry | null;
  chosen: RunChangesChosen | null;
  resume: { sessionId: string; version: number; step: string | null } | null;
  onBack: () => void;
}) {
  const client = useQueryClient();
  const settings = useQuery({ queryKey: ["experimenter", "settings"], queryFn: runApi.settings, retry: false });
  const voices = useQuery({ queryKey: ["voice", "voices"], queryFn: runApi.voices, retry: false });
  const [ambient, setAmbient] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const saved = settings.data?.ambient_mode === "noisy" ? "noisy" : "quiet";
  const shown = ambient ?? saved;
  const voiceId = settings.data?.agent_voice && settings.data.agent_voice !== "default" ? settings.data.agent_voice : voices.data?.default_voice_id;
  const voice = voices.data?.voices?.find((item) => item.voice_id === voiceId);
  const changes = (chosen?.changes ?? []).filter((item) => item.words_ko);

  async function press() {
    if (busy || !entry) return;
    // In the touch itself, before anything is awaited (lane TAB): the sound and the screen lock.
    primeStart();
    setBusy(true);
    setNote(null);
    if (shown !== saved) {
      try {
        await runApi.saveSetting("ambient_mode", shown);
        void client.invalidateQueries({ queryKey: ["experimenter", "settings"] });
      } catch {
        setBusy(false);
        setNote(copy.go.ambientFailed);
        return;
      }
    }
    go(routes.run);
    void startRun({
      protocolId: entry.protocol_id,
      changeSetId: chosen?.change_set_id ?? null,
      resume: resume ? { sessionId: resume.sessionId, version: resume.version } : null,
    });
  }

  return (
    <>
      <div className="start-summary" data-testid="start-summary">
        <p className="start-protocol-title">{entry?.title ?? ""}</p>
        {resume ? <p>{copy.go.resumeLine(resume.step)}</p> : <p>{changes.length ? copy.go.changes(changes.map((item) => `${item.step_label}단계 ${item.kind_ko} ${item.words_ko}`).join(" · ")) : copy.go.noChanges}</p>}
        {voice ? <p className="muted">{copy.go.voice(voice.label_ko)}</p> : null}
      </div>
      <AmbientChoice value={shown} onChange={setAmbient} disabled={busy || settings.isPending} />
      {note ? <Note>{note}</Note> : null}
      <Footer onBack={onBack}>
        <button type="button" className="btn btn-primary btn-big btn-start" disabled={busy || !entry?.available_for_execution} onClick={() => void press()} data-testid="start-go">
          <Play aria-hidden="true" />
          <span>{busy ? copy.go.starting : resume ? copy.go.resume : copy.go.start}</span>
        </button>
      </Footer>
    </>
  );
}

export interface StartScreenProps {
  protocolId: string | null;
  resumeId: string | null;
  step: number | null;
}

export function StartScreen({ protocolId, resumeId, step }: StartScreenProps) {
  const [chosenId, setChosenId] = useState<string | null>(protocolId);
  const [index, setIndex] = useState(() => (protocolId && step ? Math.max(0, Math.min(STEP_COUNT - 1, step - 1)) : 0));
  const [check, setCheck] = useState<StartCheck | null>(null);
  const [chosen, setChosen] = useState<RunChangesChosen | null>(null);
  const protocols = useProtocols();
  const resume = useQuery({
    queryKey: ["workspace", "experiments", resumeId ?? "", "start"],
    queryFn: () => runApi.experiment(resumeId ?? ""),
    enabled: Boolean(resumeId),
    retry: false,
    staleTime: 0,
  });
  const entry = useMemo(() => protocols.data?.find((item) => item.protocol_id === chosenId) ?? null, [protocols.data, chosenId]);
  const onLoaded = useMemo(() => (value: StartCheck) => setCheck(value), []);
  useEffect(() => {
    window.scrollTo?.(0, 0);
  }, [index]);
  useEffect(() => {
    // The shared voice code is on the page before the start press, so the press itself can make and resume the sound (lane TAB's primeRunStart).
    void loadVoiceScripts().catch(() => undefined);
  }, []);

  const back = () => (index === 0 ? go(routes.home) : setIndex(index - 1));
  const next = () => setIndex(Math.min(STEP_COUNT - 1, index + 1));

  if (resumeId) {
    // Continuing a saved experiment: its start only (the check and the safety notices were read when it began).
    if (resume.isPending || protocols.isPending) return <LoadingBox />;
    if (resume.isError || !resume.data) return <ErrorBox message={copy.go.refused} onRetry={() => void resume.refetch()} />;
    const resumed = protocols.data?.find((item) => item.protocol_id === (resume.data.protocol_id ?? chosenId)) ?? null;
    return (
      <div className="start" data-testid="start-screen" data-mode="resume">
        <section className="start-step" aria-labelledby="start-step-title">
          <p className="start-why" data-testid="start-why">{copy.why[4]}</p>
          <h1 className="screen-title" id="start-step-title">{copy.go.resume}</h1>
          <GoStep
            entry={resumed}
            chosen={null}
            resume={{ sessionId: resume.data.session_id, version: Number(resume.data.version ?? 0), step: resume.data.current_step_label ?? null }}
            onBack={() => go(routes.home)}
          />
        </section>
      </div>
    );
  }

  let body: ReactNode;
  if (index === 0 || !chosenId) {
    body = (
      <StepFrame index={0} title={copy.stepNames[0]}>
        <ProtocolStep chosen={chosenId} onChoose={setChosenId} onNext={next} onBack={back} />
      </StepFrame>
    );
  } else if (index === 1) {
    body = (
      <StepFrame index={1} title={copy.stepNames[1]}>
        <CheckStep protocolId={chosenId} onLoaded={onLoaded} onNext={next} onBack={back} />
      </StepFrame>
    );
  } else if (index === 2) {
    body = (
      <StepFrame index={2} title={copy.stepNames[2]}>
        <SafetyStep protocolId={chosenId} check={check} onNext={next} onBack={back} />
      </StepFrame>
    );
  } else if (index === 3) {
    body = (
      <StepFrame index={3} title={copy.stepNames[3]}>
        {/* Lane RX1's piece for today's values (its decision 3), the same contract the old start sheet used. */}
        <RunChangesStep
          protocolId={chosenId}
          onChosen={(value) => {
            setChosen(value);
            next();
          }}
        />
        <Footer onBack={back} />
      </StepFrame>
    );
  } else {
    body = (
      <StepFrame index={4} title={copy.stepNames[4]}>
        <GoStep entry={entry} chosen={chosen} resume={null} onBack={back} />
      </StepFrame>
    );
  }
  return (
    <div className="start" data-testid="start-screen">
      <StepProgress names={copy.stepNames} index={chosenId ? index : 0} label={copy.progressLabel} />
      {body}
    </div>
  );
}
