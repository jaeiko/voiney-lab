import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuthSession } from "../api/types";
import { fakeFetch, renderWithClient, SESSION_ACCOUNTS, SESSION_DEV } from "../test/helpers";
import { judgeVoice } from "../voice/micCheck";
import { SettingsScreen } from "./SettingsScreen";
import { micFailureWords } from "./settings/VoiceCheck";

vi.mock("../voice/engine", () => ({ openCheckMicrophone: vi.fn(async () => { throw Object.assign(new Error("no"), { name: "NotAllowedError" }); }), loadVoiceScripts: vi.fn(async () => undefined) }));

afterEach(() => {
  vi.unstubAllGlobals();
});

function table(settings: Record<string, string> = {}) {
  let saved = { confirm_mode: "readback", ambient_mode: "quiet", agent_voice: "default", voice_calibration: "none", ...settings };
  return {
    "/api/experimenter/settings": (init?: RequestInit) => {
      if (init?.method === "PUT") saved = { ...saved, ...JSON.parse(String(init.body)) };
      return { json: { settings: saved } };
    },
    "/api/voice/voices": { json: { default_voice_id: "v1", voices: [{ voice_id: "v1", label_ko: "차론 · 남성" }, { voice_id: "v2", label_ko: "코레 · 여성" }] } },
    "/api/voice/preview": { status: 503, json: { detail: "configuration_invalid" } },
  };
}

describe("SettingsScreen", () => {
  it("has the plain-word sections and no protocol choice", async () => {
    fakeFetch(table());
    renderWithClient(<SettingsScreen session={SESSION_ACCOUNTS as AuthSession} />);
    for (const title of ["목소리 맞추기", "안내 목소리", "숫자를 말했을 때", "옆 사람 말 거르기", "주변 소리 기본값", "계정"]) {
      expect(await screen.findByRole("heading", { name: title })).toBeInTheDocument();
    }
    expect(screen.queryByText(/실행할 프로토콜/)).toBeNull();
    expect(screen.getByText("재준 · 연구실 lab-a")).toBeInTheDocument();
  });

  it("saves a choice and says so; the filter stays off until the voice is checked", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch(table());
    renderWithClient(<SettingsScreen session={SESSION_ACCOUNTS as AuthSession} />);
    await user.click(await screen.findByText("먼저 물어보기"));
    await waitFor(() => expect(screen.getByTestId("settings-numbers")).toHaveTextContent("저장했어요"));
    expect(calls.some((call) => call.init?.method === "PUT" && call.init.body === JSON.stringify({ confirm_mode: "confirm" }))).toBe(true);
    await user.click(screen.getByText("시끄러워요"));
    await waitFor(() => expect(screen.getByTestId("settings-ambient")).toHaveTextContent("저장했어요"));
    expect(screen.getByTestId("filter-switch").querySelector("input")).toBeDisabled();
    expect(screen.getByTestId("settings-filter")).toHaveTextContent("목소리를 맞춘 뒤에 켤 수 있어요.");
  });

  it("picking a guiding voice saves it at once", async () => {
    const user = userEvent.setup();
    fakeFetch(table());
    renderWithClient(<SettingsScreen session={SESSION_ACCOUNTS as AuthSession} />);
    await user.click(await screen.findByText("코레 · 여성"));
    expect(await screen.findByTestId("voice-saved")).toHaveTextContent("저장했어요");
  });

  it("a blocked microphone is said in words, not the browser's", async () => {
    const user = userEvent.setup();
    fakeFetch(table());
    renderWithClient(<SettingsScreen session={SESSION_ACCOUNTS as AuthSession} />);
    await user.click(await screen.findByTestId("voice-check-start"));
    expect(await screen.findByText("마이크가 막혀 있어요. 브라우저의 마이크 권한을 허용해 주세요.")).toBeInTheDocument();
  });

  it("the development mode has no account", async () => {
    fakeFetch(table());
    renderWithClient(<SettingsScreen session={SESSION_DEV as AuthSession} />);
    expect(await screen.findByText("개발 모드에는 계정이 없어요.")).toBeInTheDocument();
  });
});

describe("the voice check", () => {
  it("judges nothing heard, too quiet and good by the voiced readings", () => {
    expect(judgeVoice([0, 0.001, 0.002])).toBe("noSound");
    expect(judgeVoice([0.01, 0.012, 0.011, 0.009])).toBe("quiet");
    expect(judgeVoice([0.05, 0.08, 0.06, 0.001])).toBe("good");
  });

  it("names the page's address when the microphone needs HTTPS", () => {
    expect(micFailureWords(new Error("이 주소에서는 마이크를 쓸 수 없어요. HTTPS 주소로 열어 주세요."), "막힘", "실패")).toContain("HTTPS");
    expect(micFailureWords(Object.assign(new Error("x"), { name: "NotFoundError" }), "막힘", "실패")).toBe("실패");
  });
});
