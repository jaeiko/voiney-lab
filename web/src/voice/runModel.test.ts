import { describe, expect, it } from "vitest";

import { applyServerEvent, EMPTY_RUN, isPaused, listeningWord, MISHEARD_SHOWN_MS, newestGeneration, nextStepWords, replyText, sourceFigures, spokenText, stepWords, timerView, type ProtocolState } from "./runModel";

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

  it("captions what was said, not the display's source quote (lane KO2, decision 4)", () => {
    const display = "실험 목적\n이 논문의 저자들은 젤 조각을 세척해요.\n\n원문 · English · PDF p.2\nYou can do this manually or with the help of a pipetting robot.";
    const said = "원문에 적힌 실험 목적이에요. 이 논문의 저자들은 젤 조각을 세척해요.";
    let model = applyServerEvent(EMPTY_RUN, { type: "server.operation", turn_id: 4, generation: 2, route: "curated_protocol", operation: "verified_fact_read" }, context);
    model = applyServerEvent(model, { type: "reply.delta", turn_id: 4, generation: 2, text: display, primary_text: said, speech_text: said }, context);
    expect(model.caption?.text).toBe(said);
    model = applyServerEvent(model, { type: "reply.complete", turn_id: 4, generation: 2, text: display, primary_text: said, speech_text: said }, context);
    expect(model.caption?.text).toBe(said);
    expect(model.log.at(-1)?.text).not.toContain("pipetting robot");
    // The answer card keeps what is shown.
    expect(model.card?.text).toBe(said);
    // A reply not said (no speech_text) is captioned with its shown words, as before.
    model = applyServerEvent(model, { type: "reply.complete", turn_id: 4, generation: 2, text: "화면에만 적었어요.", speech_text: null }, context);
    expect(model.caption?.text).toBe("화면에만 적었어요.");
    expect(spokenText({ speech_text: "  말한 글  " })).toBe("말한 글");
    // The greeting and the server's notices are captioned as said too.
    model = applyServerEvent(EMPTY_RUN, { type: "session.greeting", configuration_id: 7, turn_id: 9, generation: 1, text: "Voiney Lab입니다. “until the band is clear”인가요?", speech_text: "Voiney Lab입니다. 이 문장은 아직 번역되지 않았어요." }, context);
    expect(model.caption?.text).toBe("Voiney Lab입니다. 이 문장은 아직 번역되지 않았어요.");
    expect(spokenText({ speech_text: 3 })).toBe("");
  });

  it("marks a word the server dropped (speech.dropped, speech.ignored), not while paused, and nothing for another configuration's", () => {
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.dropped", configuration_id: 7, reason: "empty_transcript", paused: false }, context).misheardAt).toBe(1000);
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.dropped", configuration_id: 7, reason: "empty_transcript", paused: true }, context)).toBe(EMPTY_RUN);
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.dropped", configuration_id: 7, reason: "other_speaker_level" }, context)).toBe(EMPTY_RUN);
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.ignored", configuration_id: 7 }, context).misheardAt).toBe(1000);
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.ignored", configuration_id: 8 }, context)).toBe(EMPTY_RUN);
  });

  it("keeps the server's notice and reason for a dropped sound (lane HEAR2, decision 4)", () => {
    const dropped = applyServerEvent(EMPTY_RUN, { type: "speech.dropped", configuration_id: 7, reason: "minimum_voiced_frames", notice: "잘 안 들렸어요", reason_ko: "너무 짧은 소리였어요", paused: false }, context);
    expect(dropped.misheard).toEqual({ notice: "잘 안 들렸어요", reason: "너무 짧은 소리였어요" });
    // A provider that failed: its own notice and reason, in the same place.
    const failed = applyServerEvent(EMPTY_RUN, { type: "speech.dropped", configuration_id: 7, reason: "stt_failed", code: "quota_exceeded", notice: "받아쓰기 서비스가 답하지 않아요", reason_ko: "받아쓰기 서비스의 한도가 다 됐어요", paused: false }, context);
    expect(failed.misheard).toEqual({ notice: "받아쓰기 서비스가 답하지 않아요", reason: "받아쓰기 서비스의 한도가 다 됐어요" });
    // An older server without the words: the screen's own.
    expect(applyServerEvent(EMPTY_RUN, { type: "speech.ignored", configuration_id: 7 }, context).misheard).toEqual({ notice: null, reason: null });
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

  it("shows a timer paused by voice where it stands (lane HEAR2, decision 3)", () => {
    const step = { state: "paused", duration_seconds: 600, deadline_at: null, remaining_seconds: 540, name: "10분" };
    expect(timerView(state({ timers: { step } }), (s) => Number(s.remaining_seconds))).toMatchObject({ kind: "paused", seconds: 540, duration: 600, name: "10분" });
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

describe("Korean first (lane RX4, person's decision 2), as the server's fields say (lane KO2)", () => {
  // Real step sentences of four protocols from three fields (rule 10 of the lane rules, 2026-10-26), in the state the
  // merged server sends (curated_protocol.state): in-gel digestion (bio) with its reviewed Korean -- the server has taken
  // the reading's own "3단계: " off -- EAM 2.3 Analytical Solution Preparation (FDA, chemistry: steps with no number),
  // Soil nutrient analysis SOP 005 (Zenodo, chemistry / analysis: the source carries its own "1."), SDS-PAGE gel
  // electrophoresis (protocols.io, bio). No Korean is stored for the last three: primary_summary is null and
  // translation_missing is true.
  const CASES = [
    { field: "생명 · in-gel", label: "3", korean: "밴드를 Solution A 500 µL로 세척합니다. 젤 플러그를 37°C에서 15min 동안 부드럽게 교반하며 800 rpm, 37°C, 00:15:00 조건으로 배양합니다.", source: "3. Wash the band with 500 µL of Solution A. Incubate at 37 °C for 15 min with gentle shaking (800 rpm)." },
    { field: "화학·분석 · EAM 2.3", label: "", korean: null, source: "Transfer analytical portion into a digestion vessel liner (avoid placing material on liner walls; mass typically recorded to ≤0.001g)." },
    { field: "화학·분석 · Soil nutrient SOP 005", label: "1", korean: null, source: "1. Dissolve 200.1 g of NH4NO3 in 8 L deionized water." },
    { field: "생명 · SDS-PAGE", label: "2", korean: null, source: "Carefully remove the comb from the precast gel and the tape across the bottom." },
  ];

  it("shows the server's Korean large with the source under it; with none, the source at an ordinary size and 번역 준비 중", () => {
    for (const item of CASES) {
      const words = stepWords(state({ current_step_label: item.label, primary_summary: item.korean, display_summary: item.source, translation_missing: item.korean === null }));
      expect(words, item.field).toEqual(item.korean ? { korean: item.korean, source: item.source, missing: false } : { korean: null, source: item.source, missing: true });
    }
  });

  it("takes nothing off the server's Korean again (it took the step's own label off already)", () => {
    // A body that names a step itself is left as written; so is a second "N단계:" the server chose to keep.
    expect(stepWords(state({ current_step_label: "3", primary_summary: "2단계에서 만든 용액을 넣어요." })).korean).toBe("2단계에서 만든 용액을 넣어요.");
    expect(stepWords(state({ current_step_label: "3", primary_summary: "3단계: 표시된 선까지 채워요." })).korean).toBe("3단계: 표시된 선까지 채워요.");
  });

  it("takes a Korean source step as its own Korean, with nothing to fold and nothing missing", () => {
    const words = stepWords(state({ primary_summary: null, display_summary: "9. Seahorse XF glycolysis stress test 를 실행합니다", source_language: "ko", translation_missing: false }));
    expect(words).toEqual({ korean: "9. Seahorse XF glycolysis stress test 를 실행합니다", source: null, missing: false });
  });

  it("says 번역 준비 중 only when the server says the Korean is missing", () => {
    // No Korean and no word from the server (an empty source, an older state): the source, ordinary size, no claim.
    expect(stepWords(state({ primary_summary: null, display_summary: "Weigh.", translation_missing: undefined }))).toEqual({ korean: null, source: "Weigh.", missing: false });
  });

  it("takes the server's explicit next label off the body and shows the source only when the server says so", () => {
    expect(nextStepWords("5단계: 상층액을  버리고\n아세토나이트릴을 넣습니다.", false, "5")).toEqual({ korean: "상층액을 버리고 아세토나이트릴을 넣습니다.", source: null, missing: false });
    expect(nextStepWords("Step 2단계: 2단계: Add the buffer.", false, "Step 2")).toEqual({ korean: "Add the buffer.", source: null, missing: false });
    expect(nextStepWords("5. Discard the supernatant.", true, "5")).toEqual({ korean: null, source: "Discard the supernatant.", missing: true });
    expect(nextStepWords("  ", true, "5")).toEqual({ korean: null, source: null, missing: false });
  });

  it("a translation_update redraws the step with its Korean and keeps the turn's card", () => {
    let model = applyServerEvent(EMPTY_RUN, { type: "protocol.fixture.state", configuration_id: 7, state: state({ primary_summary: null, display_summary: "Incubate.", translation_missing: true }), screen: { translation_pending: true } }, context);
    model = applyServerEvent(model, { type: "server.operation", route: "curated_protocol", operation: "grounded_qa", turn_id: 5, generation: 1 }, context);
    expect(model.card?.turnId).toBe(5);
    model = applyServerEvent(model, { type: "protocol.fixture.state", configuration_id: 7, action: "translation_update", state: state({ primary_summary: "젤 조각을 둡니다.", display_summary: "Incubate.", translation_missing: false }), screen: { translation_pending: false } }, context);
    expect(stepWords(model.protocol!)).toEqual({ korean: "젤 조각을 둡니다.", source: "Incubate.", missing: false });
    expect(model.card?.turnId).toBe(5);
  });

  it("marks a reply's shown words as the source when the server says so and nothing was said aloud", () => {
    let model = applyServerEvent(EMPTY_RUN, { type: "server.operation", route: "curated_protocol", operation: "grounded_qa", turn_id: 6, generation: 1 }, context);
    model = applyServerEvent(model, { type: "reply.complete", turn_id: 6, generation: 1, text: "Incubate the gel pieces.", speech_text: null, translation_missing: true }, context);
    expect(model.caption).toMatchObject({ text: "Incubate the gel pieces.", source: true });
    expect(model.card).toMatchObject({ text: "Incubate the gel pieces.", source: true });
    model = applyServerEvent(model, { type: "reply.complete", turn_id: 7, generation: 1, text: "Incubate the gel pieces.", speech_text: "이 문장은 아직 번역되지 않았어요. 원문을 화면에 띄울게요.", translation_missing: true }, context);
    // What was said is Korean: the caption is not the source.
    expect(model.caption).toMatchObject({ text: "이 문장은 아직 번역되지 않았어요. 원문을 화면에 띄울게요.", source: false });
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
