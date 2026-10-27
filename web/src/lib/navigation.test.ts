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

  it("owns the experiment, the settings and the first guide; records and notebooks are owned too", () => {
    expect(parseRoute("#/experiment").name).toBe("run");
    expect(parseRoute("#/settings").name).toBe("settings");
    expect(parseRoute("#/welcome").name).toBe("welcome");
    expect(parseRoute("#/records").name).toBe("records");
    expect(parseRoute("#/records/s1/?from=bookmark")).toMatchObject({ name: "record", sessionId: "s1" });
    expect(parseRoute("#/records/s%201/notebook/r%202")).toMatchObject({ name: "notebook", sessionId: "s 1", reportId: "r 2" });
  });

  it("builds the start links", () => {
    expect(routes.start()).toBe("#/start");
    expect(routes.start("p1", { resume: "s9", step: 2 })).toBe("#/start/p1?resume=s9&step=2");
    expect(startHref("p 1")).toBe("#/start/p%201");
  });
});

it('keeps legacy tab bookmarks with a trailing slash and query on the new screen', () => {
  for (const [tab, name] of [['home', 'home'], ['protocols', 'protocols'], ['upload', 'upload'], ['experiment', 'run'], ['settings', 'settings']]) {
    expect(parseRoute(`#/${tab}/?from=bookmark`).name).toBe(name);
  }
});
