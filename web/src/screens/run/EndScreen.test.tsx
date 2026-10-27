import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fakeFetch, renderWithClient } from "../../test/helpers";
import { EMPTY_RUN, type ProtocolState } from "../../voice/runModel";
import { runStore } from "../../voice/runStore";
import { EndScreen, stepsWords, tookWords } from "./EndScreen";

// The end screen (lane RX4, person's decision 3): the facts from the run, the one question, the answer's body.

const STARTED = new Date(Date.now() - 23 * 60_000).toISOString();

function protocol(over: Partial<ProtocolState> = {}): ProtocolState {
  return {
    attached: true, protocol_id: "p1", revision_id: "r1", display_name: "In-gel digestion", development_only: false, active: true, total_steps: 9,
    at_final_step: false, current_step_label: "5", current_step_id: "step-5", workflow_status: "running", block_reason: null, revision: 4,
    timers: { step: null, experiment: { state: "running", started_at: STARTED, ended_at: null } },
    run_changes: [{ step_label: "4", kind_ko: "시간", words_ko: "20분" }, { step_label: "4", kind_ko: "온도", words_ko: "40 °C" }], ...over,
  };
}

function ended(over: Partial<ProtocolState> = {}, model = {}) {
  runStore.reset({
    experimentId: "s1",
    model: { ...EMPTY_RUN, protocol: protocol(over), stateRevision: 4, reportId: "ER-1", helpTurns: [4], ended: { status: "stopped", report: null }, ...model },
  });
  return runStore.get();
}

const TIMELINE = {
  timeline: [
    { event_id: "a", event_type: "observation_recorded", step_label: "2", observation: { content: "투명해졌어요", capture_source: "voice" } },
    { event_id: "b", event_type: "observation_recorded", step_label: "3", observation: { content: "거품", capture_source: "voice" } },
    { event_id: "c", event_type: "observation_recorded", step_label: "3", observation: { content: "메모", capture_source: "manual" } },
    { event_id: "d", event_type: "evidence_attached", step_label: "3", evidence: { original_filename: "gel.png" } },
  ],
};

let fetches: ReturnType<typeof fakeFetch>;
beforeEach(() => {
  try {
    localStorage.clear();
  } catch {
    /* no storage */
  }
  fetches = fakeFetch({ "/api/workspace/experiments/s1/timeline": { json: TIMELINE }, "/api/pilot/feedback": { status: 201, json: { saved: true } } });
});
afterEach(() => {
  act(() => runStore.reset());
});

describe("EndScreen", () => {
  it("says what was done -- the protocol, the steps, the time -- and three numbers from the run", async () => {
    renderWithClient(<EndScreen snapshot={ended()} />);
    expect(screen.getByTestId("run-ended")).toHaveTextContent("실험을 멈췄어요");
    expect(screen.getByTestId("end-sub")).toHaveTextContent("In-gel digestion · 9단계 중 5단계까지 · 23분 걸림");
    expect(screen.getByTestId("end-notebook")).toHaveAttribute("href", "#/records/s1");
    await waitFor(() => expect(screen.getByTestId("end-counts").querySelector('[data-count="voice"] .end-count-number')).toHaveTextContent("2"));
    expect(screen.getByTestId("end-counts").querySelector('[data-count="changes"] .end-count-number')).toHaveTextContent("2");
    expect(screen.getByTestId("end-counts").querySelector('[data-count="help"] .end-count-number')).toHaveTextContent("1");
  });

  it("a completed run names all its steps; a run with no clock has no time line", () => {
    expect(stepsWords("completed", protocol())).toBe("9단계 모두");
    expect(stepsWords("stopped", protocol({ current_step_label: "" }))).toBeNull();
    expect(tookWords(protocol({ timers: { step: null } }), Date.now())).toBeNull();
    expect(tookWords(protocol({ timers: { step: null, experiment: { started_at: STARTED, ended_at: new Date(Date.parse(STARTED) + 40_000).toISOString() } } }), Date.now())).toBe("40초 걸림");
    expect(tookWords(protocol({ timers: { step: null, experiment: { started_at: STARTED, ended_at: new Date(Date.parse(STARTED) + 65 * 60_000).toISOString() } } }), Date.now())).toBe("1시간 5분 걸림");
  });

  it("sends the rating, the chips as tags and the line to lane LOG's route, then thanks once and offers home", async () => {
    const user = userEvent.setup();
    renderWithClient(<EndScreen snapshot={ended()} />);
    expect(screen.getByTestId("end-send")).toBeDisabled();
    expect(screen.getByRole("radiogroup")).toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "편했어요" }));
    expect(screen.getByRole("radio", { name: "편했어요" })).toHaveAttribute("aria-checked", "true");
    await user.click(screen.getByRole("button", { name: "타이머가 편했어요" }));
    await user.click(screen.getByRole("button", { name: "말을 잘 알아들었어요" }));
    await user.click(screen.getByRole("button", { name: "타이머가 편했어요" }));
    await user.click(screen.getByRole("button", { name: "타이머가 편했어요" }));
    await user.type(screen.getByTestId("end-note"), "  소리가 조금 작았어요 ");
    await user.click(screen.getByTestId("end-send"));
    await waitFor(() => expect(screen.getByTestId("end-thanks")).toHaveTextContent("고마워요"));
    const posted = fetches.calls.find((call) => call.path === "/api/pilot/feedback");
    expect(JSON.parse(String(posted?.init?.body))).toEqual({ report_id: "ER-1", rating: 4, skipped: false, note: "소리가 조금 작았어요", tags: ["heard_well", "timer_easy"] });
    expect(screen.getByTestId("end-home")).toHaveAttribute("href", "#/home");
    expect(JSON.parse(localStorage.getItem("voineyFeedbackAnswered") ?? "[]")).toEqual(["ER-1"]);
  });

  it("a skip is sent as a skip, with no rating and no tags", async () => {
    const user = userEvent.setup();
    renderWithClient(<EndScreen snapshot={ended()} />);
    await user.click(screen.getByTestId("end-skip"));
    await waitFor(() => expect(screen.getByTestId("end-home")).toBeInTheDocument());
    const posted = fetches.calls.find((call) => call.path === "/api/pilot/feedback");
    expect(JSON.parse(String(posted?.init?.body))).toEqual({ report_id: "ER-1", rating: null, skipped: true, note: null, tags: [] });
    expect(screen.queryByTestId("end-thanks")).toBeNull();
  });

  it("asks nothing without a record to rate, or when this device answered about it already", () => {
    renderWithClient(<EndScreen snapshot={ended({}, { reportId: null })} />);
    expect(screen.queryByTestId("end-question")).toBeNull();
    expect(screen.getByTestId("end-home")).toBeInTheDocument();
    localStorage.setItem("voineyFeedbackAnswered", JSON.stringify(["ER-1"]));
    const { container } = renderWithClient(<EndScreen snapshot={ended()} />);
    expect(container.querySelector('[data-testid="end-question"]')).toBeNull();
  });

  it("says when the answer did not go and keeps the choices", async () => {
    fakeFetch({ "/api/workspace/experiments/s1/timeline": { json: TIMELINE }, "/api/pilot/feedback": { status: 503, json: { detail: "pilot_metrics_unavailable" } } });
    const user = userEvent.setup();
    renderWithClient(<EndScreen snapshot={ended()} />);
    await user.click(screen.getByRole("radio", { name: "보통이에요" }));
    await user.click(screen.getByTestId("end-send"));
    await waitFor(() => expect(screen.getByTestId("end-failed")).toHaveTextContent("보내지 못했어요. 다시 눌러 주세요."));
    expect(screen.getByRole("radio", { name: "보통이에요" })).toHaveAttribute("aria-checked", "true");
  });
});
