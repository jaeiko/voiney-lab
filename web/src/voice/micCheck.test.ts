// Lane HEAR2, decision 6 (2026-10-26): a failed 목소리 맞추기 says why in plain
// words. The person's test met ten "맞추지 못했어요" with no reason (the server
// answered 500). Each refusal the server can give, and a server or network
// failure, is its own reason the screen names.
import { describe, expect, it, vi } from "vitest";

import { MeasureError, measureVoice, type Recorder } from "./micCheck";

const recorder: Recorder = {
  record: async () => ({ pcm: new Int16Array(16000 * 5), micId: "mic-0011aabb" }),
  encode: () => "AAAA",
};

function answering(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })));
}

describe("measureVoice", () => {
  it("keeps the server's measurement", async () => {
    answering(200, { state: "measured", level_db: -23.4, measured_at: "2026-10-26T01:00:00+00:00", mic_id: "mic-0011aabb", speaker_filter: "off", max_age_days: 14 });
    await expect(measureVoice(recorder)).resolves.toMatchObject({ state: "measured", mic_id: "mic-0011aabb" });
  });

  it.each([
    [422, { detail: { code: "too_little_voice" } }, "too_little_voice"],
    [422, { detail: { code: "audio_length" } }, "audio_length"],
    [422, { detail: { code: "audio_invalid" } }, "audio_invalid"],
    [500, { detail: "workspace_error" }, "server"],
    [503, { detail: "configuration_invalid" }, "server"],
    [401, { detail: "authentication_required" }, "failed"],
  ])("names a %s %j refusal %s", async (status, body, reason) => {
    answering(status, body);
    await expect(measureVoice(recorder)).rejects.toMatchObject({ reason });
  });

  it("names a connection that failed", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    const failure = await measureVoice(recorder).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(MeasureError);
    expect((failure as MeasureError).reason).toBe("network");
  });
});
