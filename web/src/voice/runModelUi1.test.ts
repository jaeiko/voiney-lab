import { describe, expect, it } from "vitest";

import { applyServerEvent, EMPTY_RUN, type ProtocolState } from "./runModel";

// Lane UI1: a web lookup's free-licence photo joins the step's pictures with its credit (the old screen's
// safeWebPhoto checks), and leaves with the step.

const accepted = { configuration_id: 7, protocol_id: "p1", revision_id: "r1" };
const context = { accepted, live: () => true, now: 1000 };
const ID = "e".repeat(64);
const PHOTO = { asset_id: ID, url: `/api/web-visuals/${ID}`, mime_type: "image/png", title: "Microwave digestion vessel", author: "Lab Photographer", licence: "CC BY 4.0", page_url: "https://commons.wikimedia.org/wiki/File:Example.png" };

function state(step: string, revision: number): ProtocolState {
  return {
    attached: true, protocol_id: "p1", revision_id: "r1", source_sha256: "c".repeat(64), display_name: "EAM 2.3", development_only: false,
    active: true, total_steps: 13, at_final_step: false, current_step_label: "", current_step_id: step, primary_summary: "", display_summary: "Add water.",
    workflow_status: "running", block_reason: null, revision, timers: { step: null },
  };
}

describe("protocol.web.result", () => {
  it("adds the photo with its credit and page, and drops it when the step moves on", () => {
    let model = applyServerEvent(EMPTY_RUN, { type: "protocol.fixture.state", configuration_id: 7, state: state("eam-step-2", 3) }, context);
    model = applyServerEvent(model, { type: "protocol.web.result", configuration_id: 7, turn_id: 4, generation: 1, status: "success", subject: "digestion vessel liner", photo: PHOTO }, context);
    expect(model.figures.web).toEqual({
      key: `web:${ID}`, url: `/api/web-visuals/${ID}`, label: "Microwave digestion vessel", caption: "digestion vessel liner", origin: "web",
      credit: "출처 Wikimedia Commons · 작성자 Lab Photographer · CC BY 4.0", link: "https://commons.wikimedia.org/wiki/File:Example.png",
    });
    model = applyServerEvent(model, { type: "protocol.fixture.state", configuration_id: 7, state: state("eam-step-3", 4) }, context);
    expect(model.figures.web).toBeNull();
  });

  it("refuses another session's photo, a failed lookup and a photo that does not pass the checks", () => {
    const base = applyServerEvent(EMPTY_RUN, { type: "protocol.fixture.state", configuration_id: 7, state: state("eam-step-2", 3) }, context);
    const send = (over: Record<string, unknown>, photo: Record<string, unknown> | null = PHOTO) =>
      applyServerEvent(base, { type: "protocol.web.result", configuration_id: 7, turn_id: 4, generation: 1, status: "success", photo, ...over }, context);
    expect(send({ configuration_id: 8 })).toBe(base);
    expect(send({ status: "provider_error" })).toBe(base);
    expect(send({}, null)).toBe(base);
    expect(send({}, { ...PHOTO, url: "https://elsewhere.example/x.png" })).toBe(base);
    expect(send({}, { ...PHOTO, mime_type: "image/svg+xml" })).toBe(base);
    expect(send({}, { ...PHOTO, licence: "" })).toBe(base);
    expect(send({}, { ...PHOTO, page_url: "http://commons.wikimedia.org/x" })).toBe(base);
    expect(send({}, { ...PHOTO, asset_id: "not-a-hash" })).toBe(base);
  });
});
