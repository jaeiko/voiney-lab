import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { ApiError, postJson } from "../api/client";
import { keys, useInvite } from "../api/queries";
import type { Account } from "../api/types";
import { auth, product } from "../copy/ko";
import { Logo } from "../components/Logo";
import { Note } from "../components/Pieces";

export function authErrorWords(error: unknown): string {
  if (!(error instanceof ApiError)) return auth.errors.unknown;
  if (error.code === "login_locked") {
    const minutes = Math.max(1, Math.ceil((error.retryAfterSeconds ?? 15 * 60) / 60));
    return auth.errors.login_locked(minutes);
  }
  const table = auth.errors as Record<string, string | ((n: number) => string)>;
  const words = table[error.code];
  return typeof words === "string" ? words : auth.errors.unknown;
}

interface Props {
  invite: string | null;
  /** Shown above the form when the app reached the login after a session expired. */
  notice?: string | null;
  onSignedIn?: () => void;
}

export function LoginScreen({ invite, notice = null, onSignedIn }: Props) {
  const client = useQueryClient();
  const inviteQuery = useInvite(invite);
  const signup = Boolean(invite);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const inviteValid = !signup || inviteQuery.data?.valid === true;
  const inviteFailed = signup && (inviteQuery.isError || (inviteQuery.data && inviteQuery.data.valid !== true));

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const trimmed = name.trim();
    if (!trimmed || !password) {
      setError(auth.empty);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await postJson<{ account: Account }>(
        signup ? "/api/auth/signup" : "/api/auth/login",
        signup ? { invite, name: trimmed, password } : { name: trimmed, password },
      );
    } catch (failure) {
      setBusy(false);
      setError(authErrorWords(failure));
      return;
    }
    setBusy(false);
    if (signup) {
      // The spent invite leaves the address, so a reload never shows it again.
      window.history.replaceState(null, "", `${window.location.pathname}${window.location.hash}`);
    }
    await client.invalidateQueries({ queryKey: keys.auth });
    onSignedIn?.();
  }

  const lead = signup
    ? inviteQuery.isPending
      ? auth.inviteChecking
      : inviteValid
        ? `${auth.signupInvite(inviteQuery.data?.organization_id ?? "")}${inviteQuery.data?.admin ? ` ${auth.signupAdmin}` : ""}`
        : auth.errors.invite_invalid
    : auth.loginLead;

  return (
    <div className="auth">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand">
          <Logo height={48} />
          <p className="lead">{product.tagline}</p>
          <p className="muted">{product.principle}</p>
        </div>
        <form className="auth-form" onSubmit={(event) => void submit(event)} noValidate>
          <h1 id="auth-title" className="screen-title">
            {signup ? auth.signupTitle : auth.loginTitle}
          </h1>
          <p className="lead" data-testid="auth-lead">
            {lead}
          </p>
          {signup && inviteValid && !inviteQuery.isPending ? <p className="muted">{auth.signupLead}</p> : null}
          {notice ? <Note>{notice}</Note> : null}
          <label className="field">
            {auth.name}
            <input name="name" type="text" autoComplete="username" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            {auth.password}
            <input
              name="password"
              type="password"
              autoComplete={signup ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error ? <Note testId="auth-error">{error}</Note> : null}
          <button type="submit" className="btn btn-primary btn-wide" disabled={busy || Boolean(inviteFailed) || (signup && inviteQuery.isPending)}>
            {busy ? (signup ? auth.signingUp : auth.loggingIn) : signup ? auth.signupButton : auth.loginButton}
          </button>
          {!signup ? <p className="form-hint">{auth.loginHint}</p> : null}
        </form>
      </section>
    </div>
  );
}
