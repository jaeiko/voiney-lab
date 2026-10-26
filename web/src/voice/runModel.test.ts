import { describe, expect, it } from "vitest";

import { applyServerEvent, EMPTY_RUN, isPaused, listeningWord, MISHEARD_SHOWN_MS, newestGeneration, nextStepWords, readsKorean, replyText, sourceFigures, stepWords, timerView, withoutStepPrefix, type ProtocolState } from "./runModel";

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

  it("marks a word the server dropped (speech.dropped, speech.ignored), not while paused, and nothing for another configuration's", () => {
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.dropped", configuration_id: 7, reason: "empty_transcript", paused: false }, context).misheardAt).toBe(1000);
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.dropped", configuration_id: 7, reason: "empty_transcript", paused: true }, context)).toBe(EMPTY_RUN);
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.dropped", configuration_id: 7, reason: "other_speaker_level" }, context)).toBe(EMPTY_RUN);
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.ignored", configuration_id: 7 }, context).misheardAt).toBe(1000);
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.ignored", configuration_id: 8 }, context)).toBe(EMPTY_RUN);
  });

  it("says the side-voice filter's state for this run, nothing when it is simply off", () => {
    const words = { active: "켜짐", notMeasured: "안 맞춤", staleAge: "2주", staleDevice: "마이크 바뀜", changed: "바꿈" };
    const withWords = { ...context, filterWords: words };
    expect(applyServerEvent(EMPTY_RUN, { type: "speaker.filter", configuration_id: 7, state: "active", reason: "on" }, withWords).filterNotice).toEqual({ text: "켜짐", at: 1000 });
    expect(applyServerEvent(EMPTY_RUN, { type: "speaker.filter", configuration_id: 7, state: "inactive", reason: "stale_device" }, withWords).filterNotice?.text).toBe("마이크 바뀜");
    expect(applyServerEvent(EMPTY_RUN, { type: "speaker.filter", configuration_id: 7, state: "inactive", reason: "off" }, withWords)).toBe(EMPTY_RUN);
    expect(applyServerEvent(EMPTY_RUN, { type: "speaker.filter", configuration_id: 7, state: "lowered", words: "낮췄어요." }, withWords).filterNotice?.text).toBe("낮췄어요.");
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

// --- Lane RX4 (2026-10-26): Korean first, no repeated step label, the newest generation, the end screen's facts ---

describe("Korean first (lane RX4, person's decision 2)", () => {
  // Real step sentences of four protocols from three fields (rule 10 of the lane rules, 2026-10-26): in-gel digestion
  // (bio; its Korean is the reviewed translation of data/fixtures/development_protocols/*.localization.ko.json, which
  // carries the step's own "N단계:"), EAM 2.3 Analytical Solution Preparation (FDA, chemistry: steps with no number),
  // Soil nutrient analysis SOP 005 (Zenodo, chemistry/analysis: numbering restarts per section and the source carries its
  // own "1."), SDS-PAGE gel electrophoresis (protocols.io, bio). No Korean is stored for the last three (no model call here).
  const CASES = [
    { field: "생명 · in-gel", label: "3", korean: "3단계: 밴드를 Solution A 500 µL로 세척합니다. 젤 플러그를 37°C에서 15min 동안 부드럽게 교반하며 800 rpm, 37°C, 00:15:00 조건으로 배양합니다.", source: "3. Wash the band with 500 µL of Solution A. Incubate at 37 °C for 15 min with gentle shaking (800 rpm)." },
    { field: "화학·분석 · EAM 2.3", label: "", korean: null, source: "Transfer analytical portion into a digestion vessel liner (avoid placing material on liner walls; mass typically recorded to ≤0.001g)." },
    { field: "화학·분석 · Soil nutrient SOP 005", label: "1", korean: null, source: "1. Dissolve 200.1 g of NH4NO3 in 8 L deionized water." },
    { field: "생명 · SDS-PAGE", label: "2", korean: null, source: "Carefully remove the comb from the precast gel and the tape across the bottom." },
  ];

  it("shows the Korean large without its own label and the source under it; with no Korean the source stays at an ordinary size", () => {
    for (const item of CASES) {
      const words = stepWords(state({ current_step_label: item.label, primary_summary: item.korean ?? item.source, display_summary: item.source }));
      if (item.korean) {
        expect(words.korean, item.field).toBe(item.korean.replace(/^3단계:\s*/, ""));
        expect(words.source, item.field).toBe(item.source);
      } else {
        expect(words.korean, item.field).toBeNull();
        expect(words.source, item.field).toBe(item.source);
      }
    }
  });

  it("takes a Korean source step as its own Korean, with nothing to fold", () => {
    const words = stepWords(state({ primary_summary: "9. Seahorse XF glycolysis stress test 를 실행합니다", display_summary: "9. Seahorse XF glycolysis stress test 를 실행합니다", source_language: "ko" }));
    expect(words).toEqual({ korean: "9. Seahorse XF glycolysis stress test 를 실행합니다", source: null });
  });

  it("reads Korean as the server's is_korean does", () => {
    expect(readsKorean("젤 조각을 37 °C에서 10분 동안 둡니다.")).toBe(true);
    expect(readsKorean("9. Seahorse XF glycolysis stress test 를 실행합니다")).toBe(true);
    expect(readsKorean("Incubate the gel pieces at 37 °C for 10 min.")).toBe(false);
    expect(readsKorean("Add 1 mL H2O2 (see 주)")).toBe(false);
    expect(readsKorean("")).toBe(false);
  });

  it("strips only the named step's label from the current step, and any step's from the next (decision 5)", () => {
    expect(withoutStepPrefix("4단계: 젤 조각을 둡니다.", "4")).toBe("젤 조각을 둡니다.");
    expect(withoutStepPrefix("4a 단계 : 젤 조각을 둡니다.", "4a")).toBe("젤 조각을 둡니다.");
    expect(withoutStepPrefix("5단계: 상층액을 버립니다.", "4")).toBe("5단계: 상층액을 버립니다.");
    expect(withoutStepPrefix("이 단계: 설명", "4")).toBe("이 단계: 설명");
    expect(nextStepWords("5단계: 상층액을  버리고\n아세토나이트릴을 넣습니다.")).toEqual({ korean: "상층액을 버리고 아세토나이트릴을 넣습니다.", source: null });
    expect(nextStepWords("5. Discard the supernatant.")).toEqual({ korean: null, source: "5. Discard the supernatant." });
    expect(nextStepWords("  ")).toEqual({ korean: null, source: null });
  });
});

describe("the newest generation a bench control carries (lane RX4, decision 1)", () => {
  it("is session.ready's until a turn of this connection shows a newer one, never another connection's", () => {
    const turns = new Map<string, number>();
    expect(newestGeneration(4, turns, 11)).toBe(4);
    turns.set("11:3", 6);
    turns.set("10:9", 9);
    turns.set("11:4", 5);
    expect(newestGeneration(4, turns, 11)).toBe(6);
    expect(newestGeneration(undefined, turns, 11)).toBe(6);
    expect(newestGeneration(7, turns, 11)).toBe(7);
  });
});

describe("the end screen's facts (lane RX4, decision 3)", () => {
  it("keeps the record's id from experiment.report.state and counts a problem's help once per turn", () => {
    let model = applyServerEvent(EMPTY_RUN, { type: "experiment.report.state", configuration_id: 7, turn_id: 1, generation: 1, report: { report_id: "ER-1", status: "in_progress" } }, context);
    expect(model.reportId).toBe("ER-1");
    expect(applyServerEvent(model, { type: "experiment.report.state", configuration_id: 8, report: { report_id: "ER-2" } }, context)).toBe(model);
    model = applyServerEvent(model, { type: "turn.route_decision", turn_id: 4, action: "problem_help", intent: "problem_question" }, context);
    model = applyServerEvent(model, { type: "turn.route_decision", turn_id: 4, action: "problem_help" }, context);
    model = applyServerEvent(model, { type: "turn.route_decision", turn_id: 5, action: "next" }, context);
    model = applyServerEvent(model, { type: "turn.route_decision", configuration_id: 8, turn_id: 6, action: "problem_help" }, context);
    expect(model.helpTurns).toEqual([4]);
  });
});
