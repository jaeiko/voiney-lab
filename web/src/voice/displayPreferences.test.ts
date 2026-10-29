import { describe, expect, it } from "vitest";

import { displayModeEvent } from "./displayPreferences";

const current = { configurationId: 7, turnId: 3, generation: 2 };

describe("displayModeEvent", () => {
  it("accepts only a named mode from this configuration and current turn", () => {
    expect(displayModeEvent({ type: "screen.display_mode", mode: "far", configuration_id: 7, turn_id: 3, generation: 2 }, current)).toBe("far");
    expect(displayModeEvent({ type: "screen.display_mode", mode: "normal", configuration_id: 7, turn_id: 3, generation: 2 }, current)).toBe("normal");
    expect(displayModeEvent({ type: "screen.display_mode", mode: "far", configuration_id: 8, turn_id: 3, generation: 2 }, current)).toBeNull();
    expect(displayModeEvent({ type: "screen.display_mode", mode: "far", configuration_id: 7, turn_id: 2, generation: 2 }, current)).toBeNull();
    expect(displayModeEvent({ type: "screen.display_mode", mode: "far", configuration_id: 7, turn_id: 3, generation: 1 }, current)).toBeNull();
    expect(displayModeEvent({ type: "screen.display_mode", mode: "huge", configuration_id: 7, turn_id: 3, generation: 2 }, current)).toBeNull();
  });
});
