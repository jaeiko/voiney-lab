import { Check } from "lucide-react";

/** A flow's steps in a line, ① to ⑤: the ones done checked, the current one marked (lane RX2). */
export function StepProgress({ names, index, label }: { names: readonly string[]; index: number; label: string }) {
  return (
    <ol className="step-progress" aria-label={label} data-testid="step-progress">
      {names.map((name, at) => (
        <li key={name} data-state={at < index ? "done" : at === index ? "current" : "todo"} aria-current={at === index ? "step" : undefined}>
          <span className="step-progress-number" aria-hidden="true">
            {at < index ? <Check /> : at + 1}
          </span>
          <span className="step-progress-name">{name}</span>
        </li>
      ))}
    </ol>
  );
}
