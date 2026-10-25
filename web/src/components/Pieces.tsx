import { Check, CircleAlert, ClipboardCheck, Hourglass, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { states } from "../copy/ko";
import type { Chip as ChipData } from "../lib/protocolStatus";

// A state always has its word; a state that is not plain also has an icon
// (the design system: colour only for a state, never alone; no green --
// ready is an ink check).
const CHIP_ICONS: Record<ChipData["kind"], LucideIcon | null> = {
  ready: Check,
  review: ClipboardCheck,
  analysing: Hourglass,
  failed: CircleAlert,
  quiet: null,
};

export function Chip({ chip }: { chip: ChipData }) {
  const Icon = CHIP_ICONS[chip.kind];
  return (
    <span className="chip" data-kind={chip.kind}>
      {Icon ? <Icon aria-hidden="true" /> : null}
      <span>{chip.text}</span>
    </span>
  );
}

/** A short sentence about something that went wrong: ink, a 2 px border and an icon (the danger colour is for stop and hazard only). */
export function Note({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <p className="form-note" role="alert" data-testid={testId}>
      <CircleAlert aria-hidden="true" />
      <span>{children}</span>
    </p>
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
      <p>
        <CircleAlert aria-hidden="true" />
        <span>{message}</span>
      </p>
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
