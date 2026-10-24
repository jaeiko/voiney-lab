import type { ReactNode } from "react";

import { states } from "../copy/ko";
import type { Chip as ChipData } from "../lib/protocolStatus";

export function Chip({ chip }: { chip: ChipData }) {
  return (
    <span className="chip" data-kind={chip.kind}>
      {chip.text}
    </span>
  );
}

export function LoadingBox({ label = states.loading }: { label?: string }) {
  return (
    <div className="state-box" data-state="loading" role="status" aria-live="polite">
      <p>{label}</p>
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="state-box" data-state="error" role="alert">
      <p>{message}</p>
      {onRetry ? (
        <button type="button" className="btn" onClick={onRetry}>
          {states.reload}
        </button>
      ) : null}
    </div>
  );
}

export function EmptyBox({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <div className="state-box" data-state="empty">
      <p>{message}</p>
      {action}
    </div>
  );
}

export interface BigCardProps {
  kind: string;
  title: string;
  sub?: string | null;
  why?: string | null;
  href?: string;
  icon?: ReactNode;
}

/** The one big thing to do on a screen: a link when it goes somewhere, a plain card when it only tells. */
export function BigCard({ kind, title, sub, why, href, icon }: BigCardProps) {
  const body = (
    <>
      <span className="big-card-title">
        {icon}
        <span>{title}</span>
      </span>
      {sub ? <span className="big-card-sub">{sub}</span> : null}
      {why ? <span className="big-card-why">{why}</span> : null}
    </>
  );
  if (href) {
    return (
      <a className="big-card" data-card={kind} href={href}>
        {body}
      </a>
    );
  }
  return (
    <div className="big-card" data-card={kind} data-static="true" role="status" aria-live="polite">
      {body}
    </div>
  );
}
