import * as Dialog from "@radix-ui/react-dialog";
import { useQueryClient } from "@tanstack/react-query";
import { ClipboardList, FlaskConical, Home, NotebookPen, Settings } from "lucide-react";
import { useState, type ReactNode } from "react";

import { apiFetch } from "../api/client";
import { signedOut } from "../api/queries";
import type { AuthSession } from "../api/types";
import { frame, nav } from "../copy/ko";
import { oldRoutes, routes, type RouteName } from "../lib/navigation";
import { Logo } from "./Logo";
import { Note } from "./Pieces";

interface Props {
  session: AuthSession;
  current: RouteName;
  children: ReactNode;
}

/** The menu item a route belongs to: the upload, the analysis, the cards and today's values are the protocols'. */
export function menuKeyOf(current: RouteName): string {
  if (current === "upload" || current === "analysis" || current === "review" || current === "changes") return "protocols";
  // Lane RX2: the start flow and the run are the experiment's.
  if (current === "start" || current === "run") return "experiment";
  return current;
}

const NAV = [
  { key: "home", label: nav.home, href: routes.home, Icon: Home },
  { key: "protocols", label: nav.protocols, href: routes.protocols, Icon: ClipboardList },
  // Lane RX2: the experiment (the start flow and the run) and the settings are the new screen's own.
  { key: "experiment", label: nav.experiment, href: routes.run, Icon: FlaskConical },
  { key: "records", label: nav.records, href: oldRoutes.records, Icon: NotebookPen },
  { key: "settings", label: nav.settings, href: routes.settings, Icon: Settings },
] as const;

export function LogoutDialog() {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function logOut() {
    setBusy(true);
    setFailed(false);
    try {
      await apiFetch("/api/auth/logout", { method: "POST" });
    } catch (error) {
      // A session already gone is a sign-out too; anything else is said and the dialog stays.
      if (!(error instanceof Error && "status" in error && (error as { status: number }).status === 401)) {
        setBusy(false);
        setFailed(true);
        return;
      }
    }
    setBusy(false);
    setOpen(false);
    signedOut(client);
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button type="button" className="btn btn-quiet" data-testid="logout-open">
          {frame.logout}
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-backdrop" />
        <Dialog.Content className="dialog" aria-describedby={undefined}>
          <Dialog.Title asChild>
            <h2>{frame.logoutAsk}</h2>
          </Dialog.Title>
          {failed ? <Note>{frame.logoutFailed}</Note> : null}
          <div className="dialog-actions">
            <Dialog.Close asChild>
              <button type="button" className="btn btn-quiet" disabled={busy}>
                {frame.cancel}
              </button>
            </Dialog.Close>
            <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void logOut()} data-testid="logout-confirm">
              {frame.logoutConfirm}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function AppFrame({ session, current, children }: Props) {
  const accounts = session.mode === "accounts" && session.signed_in && session.account;
  return (
    <div className="frame">
      <header className="frame-head">
        <a className="logo" href={routes.home} aria-label={nav.home}>
          <Logo height={32} />
        </a>
        <div className="frame-account">
          {accounts ? (
            <>
              <span className="frame-account-name" data-testid="account-line">
                {session.account!.name} · {frame.labPrefix} {session.account!.organization_id}
              </span>
              {session.account!.admin ? <span className="chip" data-kind="quiet">{frame.adminChip}</span> : null}
              <LogoutDialog />
            </>
          ) : (
            <span className="chip" data-kind="quiet" data-testid="dev-chip">
              {frame.devChip}
            </span>
          )}
        </div>
      </header>
      <div className="frame-body">
        <nav className="nav" aria-label={nav.menuLabel}>
          {NAV.map(({ key, label, href, Icon }) => (
            <a key={key} href={href} aria-current={menuKeyOf(current) === key ? "page" : undefined} data-nav={key}>
              <Icon aria-hidden="true" />
              <span>{label}</span>
            </a>
          ))}
        </nav>
        <main className="frame-main" id="main">
          {children}
        </main>
      </div>
    </div>
  );
}
