import type { LucideIcon } from "lucide-react";
import { forwardRef, useRef, type AnchorHTMLAttributes, type ButtonHTMLAttributes, type InputHTMLAttributes, type KeyboardEvent, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

// The shared controls (lane UI1, person's request of 2026-10-31): the end of a
// run, the records, a value's 확인/고치기, the rating, the exports and the
// binder use one button, one rating scale and one field, after the design
// system (docs/UI_SPEC.md 6: ink on paper, colour only for a state, 4 px
// corners, Pretendard 400/600/700, targets of 48 px or more). One primary
// button per screen part -- the one thing to do next -- the rest secondary,
// quiet or text. Styles: styles/controls.css.

export type ButtonVariant = "primary" | "secondary" | "quiet" | "text";
export type ButtonSize = "normal" | "big";

function buttonClass(variant: ButtonVariant, size: ButtonSize, extra?: string): string {
  return ["btn", variant === "secondary" ? null : `btn-${variant}`, size === "big" ? "btn-big" : null, extra].filter(Boolean).join(" ");
}

interface Look {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** An icon beside the word (never alone: the design system's icons always have a word). */
  icon?: LucideIcon;
}

export type ButtonProps = Look & ButtonHTMLAttributes<HTMLButtonElement>;

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "normal", icon: Icon, className, type = "button", children, ...rest },
  ref,
) {
  return (
    <button ref={ref} type={type} className={buttonClass(variant, size, className)} data-variant={variant} {...rest}>
      {Icon ? <Icon aria-hidden="true" /> : null}
      {children}
    </button>
  );
});

export type ButtonLinkProps = Look & AnchorHTMLAttributes<HTMLAnchorElement>;

/** A link that looks like a button (it goes somewhere, or downloads). */
export function ButtonLink({ variant = "secondary", size = "normal", icon: Icon, className, children, ...rest }: ButtonLinkProps) {
  return (
    <a className={buttonClass(variant, size, className)} data-variant={variant} {...rest}>
      {Icon ? <Icon aria-hidden="true" /> : null}
      {children}
    </a>
  );
}

export interface RatingScaleProps {
  /** The chosen value, 1..labels.length, or null. */
  value: number | null;
  onChange: (value: number) => void;
  /** One word per point, lowest first. */
  labels: readonly string[];
  /** An icon per point, beside its word. */
  icons?: readonly LucideIcon[];
  disabled?: boolean;
  /** The id of the question it answers. */
  labelledBy: string;
  testId?: string;
}

/**
 * The 1–5 scale: one row of equal tiles, a radio group (arrow keys move, the chosen tile is ink). Every tile is at
 * least 56 px high and says its word; on a phone the tiles stack.
 */
export function RatingScale({ value, onChange, labels, icons, disabled, labelledBy, testId }: RatingScaleProps) {
  const tiles = useRef<Array<HTMLButtonElement | null>>([]);
  function move(event: KeyboardEvent<HTMLButtonElement>, at: number) {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = Math.min(labels.length - 1, Math.max(0, at + step));
    onChange(next + 1);
    tiles.current[next]?.focus();
  }
  return (
    <div className="rating-scale" role="radiogroup" aria-labelledby={labelledBy} data-testid={testId} style={{ ["--points" as string]: labels.length }}>
      {labels.map((word, at) => {
        const Icon = icons?.[at];
        const point = at + 1;
        const chosen = value === point;
        return (
          <button
            key={word}
            ref={(node) => {
              tiles.current[at] = node;
            }}
            type="button"
            role="radio"
            aria-checked={chosen}
            tabIndex={chosen || (value === null && at === 0) ? 0 : -1}
            className="rating-tile"
            data-rating={point}
            disabled={disabled}
            onClick={() => onChange(point)}
            onKeyDown={(event) => move(event, at)}
          >
            {Icon ? <Icon aria-hidden="true" /> : null}
            <span className="rating-point" aria-hidden="true">{point}</span>
            <span className="rating-word">{word}</span>
          </button>
        );
      })}
    </div>
  );
}

export interface FieldProps {
  label: ReactNode;
  className?: string;
  testId?: string;
  children: ReactNode;
}

/** A label above its control (input, select, text area). */
export function Field({ label, className, testId, children }: FieldProps) {
  return (
    <label className={["field", className].filter(Boolean).join(" ")} data-testid={testId}>
      <span className="field-label">{label}</span>
      {children}
    </label>
  );
}

export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function TextInput({ className, ...rest }, ref) {
  return <input ref={ref} className={["field-control", className].filter(Boolean).join(" ")} {...rest} />;
});

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={["field-control", "field-select", className].filter(Boolean).join(" ")} {...rest}>
      {children}
    </select>
  );
}

export function TextArea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={["field-control", "field-textarea", className].filter(Boolean).join(" ")} {...rest} />;
}

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: ReactNode;
}

/** A 24 px box with its words beside it, the whole line a 48 px target. */
export function Checkbox({ label, className, ...rest }: CheckboxProps) {
  return (
    <label className={["check", className].filter(Boolean).join(" ")}>
      <input type="checkbox" className="check-box" {...rest} />
      <span className="check-words">{label}</span>
    </label>
  );
}
