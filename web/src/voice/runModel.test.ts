import { describe, expect, it } from "vitest";

import { applyServerEvent, EMPTY_RUN, isPaused, listeningWord, MISHEARD_SHOWN_MS, replyText, sourceFigures, timerView, type ProtocolState } from "./runModel";

const accepted = { configuration_id: 7, protocol_id: "p1", revision_id: "r1" };
const live = () => true;
const context = { accepted, live, now: 1000 };

function state(over: Partial<ProtocolState> = {}): ProtocolState {
  return {
    attached: true, protocol_id: "p1", revision_id: "r1", source_sha256: "c".repeat(64), display_name: "In-gel digestion", development_only: false,
    active: true, total_steps: 9, at_final_step: false, current_step_label: "4", current_step_id: "step-4", primary_summary: "젤 조각을 둡니다.",
    display_summary: "Incubate.", workflow_status: "running", block_reason: null, revision: 3, timers: { step: null }, ...over,
  };
}

describe("applyServerEvent", () => {
  it("takes the accepted configuration's protocol state and refuses an older one or another protocol's", () => {
    let model = applyServerEvent(EMPTY_RUN, { type: "protocol.fixture.state", configuration_id: 7, state: state(), screen: { next_step_sentence: "5단계" } }, context);
    expect(model.protocol?.current_step_label).toBe("4");
    expect(model.screen?.next_step_sentence).toBe("5단계");
    const older = applyServerEvent(model, { type: "protocol.fixture.state", configuration_id: 7, state: state({ revision: 2, current_step_label: "3" }) }, context);
    expect(older).toBe(model);
    expect(applyServerEvent(model, { type: "protocol.fixture.state", configuration_id: 8, state: state({ revision: 9 }) }, context)).toBe(model);
    expect(applyServerEvent(model, { type: "protocol.fixture.state", configuration_id: 7, state: state({ revision: 9, protocol_id: "p2" }) }, context)).toBe(model);
    model = applyServerEvent(model, { type: "protocol.fixture.state", configuration_id: 7, state: state({ revision: 4, current_step_label: "5", current_step_id: "step-5" }) }, context);
    expect(model.protocol?.current_step_label).toBe("5");
  });

  it("logs what was heard and said, with the caption on the latest", () => {
    let model = applyServerEvent(EMPTY_RUN, { type: "session.greeting", configuration_id: 7, turn_id: 1, generation: 1, text: "안녕하세요." }, context);
    expect(model.caption).toEqual({ key: "said:1:1", who: "said", text: "안녕하세요." });
    model = applyServerEvent(model, { type: "transcript", turn_id: 2, generation: 1, text: "다음 단계" }, context);
    expect(model.caption?.who).toBe("heard");
    model = applyServerEvent(model, { type: "reply.delta", turn_id: 2, generation: 1, text: "5단계로 넘어왔어요." }, context);
    model = applyServerEvent(model, { type: "reply.delta", turn_id: 2, generation: 1, text: "화면을 봐 주세요." }, context);
    expect(model.caption?.text).toBe("5단계로 넘어왔어요. 화면을 봐 주세요.");
    expect(model.log.map((line) => line.who)).toEqual(["said", "heard", "said"]);
    // A turn the audio no longer plays (cut off, stale) is not drawn.
    expect(applyServerEvent(model, { type: "transcript", turn_id: 3, generation: 1, text: "x" }, { ...context, live: () => false })).toBe(model);
  });

  it("marks a word the server dropped, and nothing for another configuration's", () => {
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.ignored", configuration_id: 7 }, context).misheardAt).toBe(1000);
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.ignored", configuration_id: 8 }, context)).toBe(EMPTY_RUN);
  });

  it("keeps the end, a refusal's own words and a timer notice", () => {
    expect(applyServerEvent(EMPTY_RUN, { type: "experiment.ended", configuration_id: 7, status: "stopped", report: "saved" }, context).ended).toEqual({ status: "stopped", report: "saved" });
    expect(applyServerEvent(EMPTY_RUN, { type: "workflow.control.refused", configuration_id: 7, message_ko: "지금은 멈출 수 없어요." }, context).notice).toBe("지금은 멈출 수 없어요.");
    expect(applyServerEvent(EMPTY_RUN, { type: "protocol.timer.notice", configuration_id: 7, text: "1분 남았어요", step_id: "step-4" }, context).timerNotice).toEqual({ text: "1분 남았어요", stepId: "step-4" });
  });

  it("opens a card for a confirmation turn and fills its candidates from that turn's screen fields", () => {
    let model = applyServerEvent(EMPTY_RUN, { type: "protocol.fixture.state", configuration_id: 7, state: state() }, context);
    model = applyServerEvent(model, { type: "server.operation", route: "curated_protocol", operation: "reference_clarification_required", turn_id: 5, generation: 1 }, context);
    expect(model.card?.kind).toBe("clarify");
    model = applyServerEvent(model, { type: "protocol.fixture.state", configuration_id: 7, turn_id: 5, state: state({ revision: 4 }), screen: { clarify_candidates: ["TBST", "PBS"] } }, context);
    expect(model.card?.candidates).toEqual(["TBST", "PBS"]);
    expect(applyServerEvent(model, { type: "server.operation", route: "curated_protocol", operation: "next_step_transition", turn_id: 6 }, context).card).toBeNull();
  });
});

describe("timerView and listeningWord", () => {
  it("shows no timer when the step has none, a chip while it runs, its last minute and its end", () => {
    expect(timerView(state(), () => 0).kind).toBe("none");
    const step = { state: "running", duration_seconds: 600, deadline_at: "x" };
    expect(timerView(state({ timers: { step } }), () => 510)).toMatchObject({ kind: "running", seconds: 510, duration: 600 });
    expect(timerView(state({ timers: { step } }), () => 45).kind).toBe("soon");
    expect(timerView(state({ timers: { step } }), () => -20)).toMatchObject({ kind: "done", seconds: 20 });
  });

  it("puts the pause first, then a dropped word for a few seconds, then the voice state", () => {
    expect(isPaused(state({ workflow_status: "paused" }))).toBe(true);
    expect(listeningWord("LISTENING", true, null, 0)).toBe("paused");
    expect(listeningWord("LISTENING", false, 1000, 1000 + MISHEARD_SHOWN_MS - 1)).toBe("misheard");
    expect(listeningWord("LISTENING", false, 1000, 1000 + MISHEARD_SHOWN_MS)).toBe("listening");
    expect(listeningWord("THINKING", false, null, 0)).toBe("thinking");
    expect(listeningWord("SPEAKING", false, null, 0)).toBe("speaking");
    expect(listeningWord("RECONNECTING", true, null, 0)).toBe("reconnecting");
  });
});

describe("pictures and words", () => {
  it("takes only the source figures of this protocol and revision, at their own address", () => {
    const good = { kind: "source_figure", protocol_id: "p1", revision_id: "r1", figure_id: "source-figure-2-1", mime_type: "image/png", label: "그림 1", caption: "Fig 1", url: "/api/protocols/p1/revisions/r1/figures/source-figure-2-1" };
    expect(sourceFigures(state(), { source_figures: [good] })).toEqual([{ key: "figure:source-figure-2-1", url: good.url, label: "그림 1", caption: "Fig 1", origin: "source" }]);
    expect(sourceFigures(state(), { source_figures: [{ ...good, url: "https://elsewhere.example/x.png" }] })).toEqual([]);
    expect(sourceFigures(state(), { source_figures: [{ ...good, revision_id: "r0" }] })).toEqual([]);
  });

  it("reads a reply's display document before its text", () => {
    expect(replyText({ display_document: { sections: [{ kind: "paragraph", text: "첫 줄" }, { kind: "source", text: "원문" }, { kind: "bullets", items: ["a", "b"] }] } })).toBe("첫 줄\na\nb");
    expect(replyText({ text: "## 제목\n본문" })).toBe("제목\n본문");
  });
});
