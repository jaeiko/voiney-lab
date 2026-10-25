import { Check } from "lucide-react";
import { useState } from "react";

import { useProtocolDetail } from "../api/queries";
import type { RunChangesChosen } from "../api/types";
import { RunChangesStep } from "../components/RunChangesStep";
import { TitleLine } from "../components/TitleLine";
import { changes as words } from "../copy/ko";
import { startHref } from "../lib/navigation";

/**
 * "오늘 원문과 다르게 할 값" as its own screen (lane RX1, decision 3). The
 * piece inside it, RunChangesStep, is what lane RX2's start flow puts
 * between the safety statements and the start; here the choice ends with
 * the words of what was chosen and [실험 시작].
 */
export function RunChangesScreen({ protocolId }: { protocolId: string }) {
  const detail = useProtocolDetail(protocolId, true, false);
  const [chosen, setChosen] = useState<RunChangesChosen | null>(null);
  const entry = detail.data;

  return (
    <section aria-labelledby="protocol-title">
      <p className="screen-why">{words.why}</p>
      {entry ? (
        <TitleLine entry={entry} as="h2" renamable={false} />
      ) : (
        <h1 id="protocol-title" className="screen-title">
          {words.heading}
        </h1>
      )}
      <h1 className="screen-title" style={{ marginBottom: "var(--space-4)" }}>
        {words.heading}
      </h1>
      {chosen ? (
        <div className="state-box" data-state="done" data-testid="changes-chosen">
          <p>
            <Check aria-hidden="true" />
            <span>{words.chosenDoneHeading}</span>
          </p>
          <p className="muted">{chosen.changes.length ? words.chosenDoneSome(chosen.changes.length) : words.chosenDoneNone}</p>
          {chosen.changes.length ? (
            <ul className="chosen-words">
              {chosen.changes.map((change) => (
                <li key={change.item_id}>
                  {words.stepOf(change.step_label)} {change.kind_ko ?? ""} · {change.words_ko ?? `${change.source_shown_ko ?? ""} → ${change.shown_ko ?? ""}`}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="card-choices">
            <a className="btn btn-primary" href={startHref(protocolId)} data-testid="changes-start">
              {words.start}
            </a>
          </div>
        </div>
      ) : (
        <RunChangesStep protocolId={protocolId} onChosen={setChosen} />
      )}
    </section>
  );
}
