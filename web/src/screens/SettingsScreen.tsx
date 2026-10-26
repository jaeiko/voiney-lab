import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";

import { ApiError } from "../api/client";
import { signedOut } from "../api/queries";
import { runApi, serverWords } from "../api/runApi";
import type { AuthSession } from "../api/types";
import { LogoutDialog } from "../components/AppFrame";
import { ErrorBox, LoadingBox, Note } from "../components/Pieces";
import { settings as copy, frame } from "../copy/ko";
import { uploadedDay } from "../lib/protocolStatus";
import { parseVoiceReference, referenceStale } from "../voice/micCheck";
import { SETTINGS_KEY, VoicePicker } from "./settings/VoicePicker";
import { VoiceCheck } from "./settings/VoiceCheck";

// The settings (lane RX2, person's decision 5 of 2026-10-25), in plain words
// (docs/COPY_DECK.md section 3): the voice check, the guiding voice, what is
// done when a number is said, the filter for a neighbour's voice, the noise
// default and the account. Each choice is the server's setting
// (/api/experimenter/settings) and says "저장했어요" when the server took it.
// The protocol to run is not chosen here (the start flow does it).

function Section({ title, lead, children, id }: { title: string; lead?: string; children: ReactNode; id: string }) {
  return (
    <section className="settings-section" aria-labelledby={`${id}-title`} data-testid={`settings-${id}`}>
      <h2 id={`${id}-title`}>{title}</h2>
      {lead ? <p className="muted">{lead}</p> : null}
      {children}
    </section>
  );
}

function Segmented({ name, value, options, onChange, disabled = false }: { name: string; value: string; options: Array<{ value: string; label: string }>; onChange: (value: string) => void; disabled?: boolean }) {
  return (
    <div className="segmented" role="radiogroup" aria-label={name} aria-disabled={disabled || undefined}>
      {options.map((option) => (
        <label key={option.value} className="segmented-option" data-checked={value === option.value} data-disabled={disabled || undefined}>
          <input type="radio" name={name} value={option.value} checked={value === option.value} disabled={disabled} onChange={() => onChange(option.value)} />
          <span>{option.label}</span>
        </label>
      ))}
    </div>
  );
}

function SavedLine({ state }: { state: "saved" | "failed" | null }) {
  if (state === "saved") return <p className="saved" role="status">{copy.saved}</p>;
  if (state === "failed") return <Note>{copy.saveFailed}</Note>;
  return null;
}

function useSetting(name: string) {
  const client = useQueryClient();
  const [state, setState] = useState<"saved" | "failed" | null>(null);
  async function save(value: string) {
    setState(null);
    try {
      client.setQueryData(SETTINGS_KEY, await runApi.saveSetting(name, value));
      setState("saved");
    } catch {
      setState("failed");
      void client.invalidateQueries({ queryKey: SETTINGS_KEY });
    }
  }
  return { state, save };
}

function PasswordForm() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; failed: boolean } | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!current || !next) {
      setNote({ text: copy.account.passwordEmpty, failed: true });
      return;
    }
    setBusy(true);
    try {
      await runApi.changePassword(current, next);
      setCurrent("");
      setNext("");
      setNote({ text: copy.account.passwordDone, failed: false });
    } catch (error) {
      const code = error instanceof ApiError ? error.code : "";
      setNote({ text: code === "network" ? copy.account.network : copy.account.passwordErrors[code] ?? copy.account.passwordErrors.unknown, failed: true });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="settings-account-part">
      <button type="button" className="btn" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="password-open">
        {copy.account.password}
      </button>
      {open ? (
        <form className="settings-form" onSubmit={(event) => void submit(event)}>
          <label className="field">
            <span>{copy.account.passwordCurrent}</span>
            <input type="password" autoComplete="current-password" value={current} onChange={(event) => setCurrent(event.target.value)} />
          </label>
          <label className="field">
            <span>{copy.account.passwordNew}</span>
            <input type="password" autoComplete="new-password" value={next} onChange={(event) => setNext(event.target.value)} />
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {copy.account.passwordSave}
          </button>
        </form>
      ) : null}
      {note ? note.failed ? <Note>{note.text}</Note> : <p className="saved" role="status">{note.text}</p> : null}
    </div>
  );
}

function DeleteForm() {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!password) {
      setNote(copy.account.deleteErrors.password_invalid);
      return;
    }
    setBusy(true);
    try {
      await runApi.deleteAccount(password);
      signedOut(client);
    } catch (error) {
      const code = error instanceof ApiError ? error.code : "";
      setNote(serverWords(error) ?? (code === "network" ? copy.account.network : copy.account.deleteErrors[code] ?? copy.account.deleteErrors.unknown));
    } finally {
      setPassword("");
      setBusy(false);
    }
  }
  return (
    <div className="settings-account-part">
      <button type="button" className="btn btn-quiet" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="delete-open">
        {copy.account.delete}
      </button>
      {open ? (
        <form className="settings-form" onSubmit={(event) => void submit(event)}>
          <p>{copy.account.deleteWarning}</p>
          <label className="field">
            <span>{copy.account.deletePassword}</span>
            <input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          <div className="settings-form-actions">
            <button type="button" className="btn btn-quiet" onClick={() => setOpen(false)}>
              {frame.cancel}
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {copy.account.deleteConfirm}
            </button>
          </div>
        </form>
      ) : null}
      {note ? <Note>{note}</Note> : null}
    </div>
  );
}

export function SettingsScreen({ session }: { session: AuthSession }) {
  const settings = useQuery({ queryKey: SETTINGS_KEY, queryFn: runApi.settings, retry: false });
  const numbers = useSetting("confirm_mode");
  const ambient = useSetting("ambient_mode");
  const accounts = session.mode === "accounts" && session.signed_in && session.account;
  const filter = useSetting("speaker_filter");
  const client = useQueryClient();
  // Lane HEAR decision 2: the measured voice the server keeps (level, when, which microphone); 14 days at most.
  const reference = parseVoiceReference(settings.data?.voice_reference);
  const stale = reference !== null && referenceStale(reference);
  const filterOn = settings.data?.speaker_filter === "on";
  return (
    <div className="settings" data-testid="settings-screen">
      <h1 className="screen-title">{copy.title}</h1>
      <Section id="voice-check" title={copy.voiceCheck.title} lead={copy.voiceCheck.lead}>
        <VoiceCheck onMeasured={() => void client.invalidateQueries({ queryKey: SETTINGS_KEY })} />
      </Section>
      <Section id="agent-voice" title={copy.agentVoice.title} lead={copy.agentVoice.lead}>
        <VoicePicker />
      </Section>
      {settings.isPending ? (
        <LoadingBox />
      ) : settings.isError ? (
        <ErrorBox message={copy.loadFailed} onRetry={() => void settings.refetch()} />
      ) : (
        <>
          <Section id="numbers" title={copy.numbers.title}>
            <Segmented
              name={copy.numbers.title}
              value={settings.data.confirm_mode ?? "readback"}
              options={[
                { value: "readback", label: copy.numbers.readback },
                { value: "confirm", label: copy.numbers.confirm },
                { value: "quiet", label: copy.numbers.quiet },
              ]}
              onChange={(value) => void numbers.save(value)}
            />
            <SavedLine state={numbers.state} />
          </Section>
          <Section id="filter" title={copy.filter.title} lead={copy.filter.lead}>
            <div data-testid="filter-switch">
              <Segmented
                name={copy.filter.title}
                value={filterOn ? "on" : "off"}
                disabled={!reference && !filterOn}
                options={[
                  { value: "off", label: copy.filter.off },
                  { value: "on", label: copy.filter.on },
                ]}
                onChange={(value) => void filter.save(value)}
              />
            </div>
            {!reference ? (
              <p className="muted" id="filter-why">{copy.filter.needsCheck}</p>
            ) : stale ? (
              <Note testId="filter-stale">{copy.filter.stale}</Note>
            ) : (
              <p className="muted" id="filter-why">{copy.filter.measuredOn(uploadedDay(new Date(reference.measuredAt).toISOString()) ?? "")}</p>
            )}
            <SavedLine state={filter.state} />
          </Section>
          <Section id="ambient" title={copy.ambient.title} lead={copy.ambient.lead}>
            <Segmented
              name={copy.ambient.title}
              value={settings.data.ambient_mode === "noisy" ? "noisy" : "quiet"}
              options={[
                { value: "quiet", label: copy.ambient.quiet },
                { value: "noisy", label: copy.ambient.noisy },
              ]}
              onChange={(value) => void ambient.save(value)}
            />
            <p className="muted">{copy.ambient.noisyWhy}</p>
            <SavedLine state={ambient.state} />
          </Section>
        </>
      )}
      <Section id="account" title={copy.account.title}>
        {accounts ? (
          <>
            <p className="settings-account-name">{copy.account.name(session.account!.name, session.account!.organization_id)}</p>
            <div className="settings-account">
              <PasswordForm />
              <div className="settings-account-part">
                <LogoutDialog />
              </div>
              <DeleteForm />
            </div>
          </>
        ) : (
          <p className="muted">{copy.account.devMode}</p>
        )}
      </Section>
    </div>
  );
}
