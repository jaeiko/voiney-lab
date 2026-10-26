import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthSession } from "../api/types";
import { fakeFetch, renderWithClient, SESSION_ACCOUNTS, SESSION_DEV } from "../test/helpers";
import { markWelcomeDone, welcomeDue, welcomeKey, WelcomeScreen } from "./WelcomeScreen";

vi.mock("../voice/engine", () => ({ recordVoice: vi.fn(), encodePcm: vi.fn(), loadVoiceScripts: vi.fn(async () => undefined) }));

const session = SESSION_ACCOUNTS as AuthSession;

beforeEach(() => {
  localStorage.clear();
  fakeFetch({
    "/api/experimenter/settings": { json: { settings: { agent_voice: "default" } } },
    "/api/voice/voices": { json: { default_voice_id: "v1", voices: [{ voice_id: "v1", label_ko: "차론 · 남성" }] } },
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the first guide", () => {
  it("is due once per account after a login, never in the development mode", () => {
    expect(welcomeDue(session)).toBe(true);
    markWelcomeDone(session);
    expect(localStorage.getItem(welcomeKey("a1"))).toBe("done");
    expect(welcomeDue(session)).toBe(false);
    expect(welcomeDue(SESSION_DEV as AuthSession)).toBe(false);
  });

  it("asks for the microphone, offers the voice check with a skip, then the guiding voice, then home", async () => {
    const user = userEvent.setup();
    renderWithClient(<WelcomeScreen session={session} allow={async () => "USB 마이크"} />);
    expect(screen.getByTestId("welcome-why")).toHaveTextContent("마이크가 필요해요");
    expect(screen.getByTestId("welcome-next")).toBeDisabled();
    await user.click(screen.getByTestId("welcome-mic"));
    expect(await screen.findByTestId("welcome-mic-ok")).toHaveTextContent("마이크를 쓸 수 있어요 · USB 마이크");
    await user.click(screen.getByTestId("welcome-next"));
    expect(screen.getByTestId("welcome-why")).toHaveTextContent("건너뛰어도 돼요");
    await user.click(screen.getByTestId("voice-check-skip"));
    expect(await screen.findByTestId("voice-picker")).toBeInTheDocument();
    await user.click(screen.getByTestId("welcome-done"));
    expect(welcomeDue(session)).toBe(false);
    expect(window.location.hash).toBe("#/home");
  });

  it("says where to allow a refused microphone", async () => {
    const user = userEvent.setup();
    renderWithClient(<WelcomeScreen session={session} allow={async () => { throw Object.assign(new Error("denied"), { name: "NotAllowedError" }); }} />);
    await user.click(screen.getByTestId("welcome-mic"));
    expect(await screen.findByText("마이크가 막혀 있어요. 브라우저의 마이크 권한을 허용한 뒤 다시 눌러 주세요.")).toBeInTheDocument();
  });
});
