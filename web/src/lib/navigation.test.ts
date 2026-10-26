import { describe, expect, it } from "vitest";

import { parseRoute, routes, startHref } from "./navigation";

describe("lane RX2's routes", () => {
  it("reads the start flow, its protocol, a continued experiment and a step to reopen", () => {
    expect(parseRoute("#/start")).toMatchObject({ name: "start", protocolId: null, resumeId: null, step: null });
    expect(parseRoute("#/start/p%201")).toMatchObject({ name: "start", protocolId: "p 1" });
    expect(parseRoute("#/start/p1?resume=s9")).toMatchObject({ name: "start", protocolId: "p1", resumeId: "s9" });
    expect(parseRoute("#/start/p1?step=3")).toMatchObject({ step: 3 });
    expect(parseRoute("#/start/p1?step=9").step).toBeNull();
  });

  it("owns the experiment, the settings and the first guide; the records stay the old screen's", () => {
    expect(parseRoute("#/experiment").name).toBe("run");
    expect(parseRoute("#/settings").name).toBe("settings");
    expect(parseRoute("#/welcome").name).toBe("welcome");
    expect(parseRoute("#/records").name).toBe("handoff");
  });

  it("builds the start links", () => {
    expect(routes.start()).toBe("#/start");
    expect(routes.start("p1", { resume: "s9", step: 2 })).toBe("#/start/p1?resume=s9&step=2");
    expect(startHref("p 1")).toBe("#/start/p%201");
  });
});
