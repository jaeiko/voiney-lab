import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuthSession } from "../api/types";
import { fakeFetch, renderWithClient, SESSION_ACCOUNTS, SESSION_DEV } from "../test/helpers";
import { parseVoiceReference, referenceStale } from "../voice/micCheck";
import { SettingsScreen } from "./SettingsScreen";
import { micFailureWords } from "./settings/VoiceCheck";

const engine = vi.hoisted(() => ({
  recordVoice: vi.fn(async (): Promise<{ pcm: Int16Array; micId: string | null }> => { throw Object.assign(new Error("no"), { name: "NotAllowedError" }); }),
  encodePcm: vi.fn(() => "AAAA"),
  loadVoiceScripts: vi.fn(async () => undefined),
}));
vi.mock("../voice/engine", () => engine);
const NOW_REFERENCE = `v1:-23.4:${Math.floor(Date.now() / 1000) - 3600}:mic-0011aabb`;

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

  it("saves a choice and says so; the filter cannot be turned on until the voice is measured", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch(table());
    renderWithClient(<SettingsScreen session={SESSION_ACCOUNTS as AuthSession} />);
    await user.click(await screen.findByText("먼저 물어보기"));
    await waitFor(() => expect(screen.getByTestId("settings-numbers")).toHaveTextContent("저장했어요"));
    expect(calls.some((call) => call.init?.method === "PUT" && call.init.body === JSON.stringify({ confirm_mode: "confirm" }))).toBe(true);
    await user.click(screen.getByText("시끄러워요"));
    await waitFor(() => expect(screen.getByTestId("settings-ambient")).toHaveTextContent("저장했어요"));
    expect(screen.getByTestId("filter-switch").querySelector('input[value="on"]')).toBeDisabled();
    expect(screen.getByTestId("settings-filter")).toHaveTextContent("목소리를 맞춘 뒤에 켤 수 있어요.");
  });

  it("a measured voice lets the filter be turned on, saved as speaker_filter; an old one asks to measure again", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch(table({ voice_calibration: "measured", voice_reference: NOW_REFERENCE }));
    renderWithClient(<SettingsScreen session={SESSION_ACCOUNTS as AuthSession} />);
    await user.click(await screen.findByText("켜기"));
    await waitFor(() => expect(screen.getByTestId("settings-filter")).toHaveTextContent("저장했어요"));
    expect(calls.some((call) => call.init?.method === "PUT" && call.init.body === JSON.stringify({ speaker_filter: "on" }))).toBe(true);
    vi.unstubAllGlobals();
    fakeFetch(table({ voice_calibration: "measured", voice_reference: "v1:-23.4:1700000000:mic-0011aabb", speaker_filter: "on" }));
    renderWithClient(<SettingsScreen session={SESSION_ACCOUNTS as AuthSession} />);
    expect(await screen.findByTestId("filter-stale")).toHaveTextContent("맞춘 지 2주가 지났어요. 다시 맞춰 주세요");
  });

  it("목소리 맞추기 records the sentence and posts it with the microphone's id; the server's too_little_voice is said", async () => {
    const user = userEvent.setup();
    engine.recordVoice.mockImplementation(async () => ({ pcm: new Int16Array(16000), micId: "mic-0011aabb" }));
    const { calls } = fakeFetch({
      ...table(),
      "/api/voice/calibration": { json: { state: "measured", level_db: -23.4, measured_at: "2026-10-26T01:00:00+00:00", mic_id: "mic-0011aabb", speaker_filter: "off", max_age_days: 14 } },
    });
    renderWithClient(<SettingsScreen session={SESSION_ACCOUNTS as AuthSession} />);
    await user.click(await screen.findByTestId("voice-check-start"));
    expect(await screen.findByTestId("voice-check-result")).toHaveTextContent("목소리를 맞췄어요");
    const posted = calls.find((call) => call.path === "/api/voice/calibration");
    expect(JSON.parse(String(posted?.init?.body))).toEqual({ pcm_base64: "AAAA", mic_id: "mic-0011aabb" });
    expect(engine.recordVoice).toHaveBeenCalledWith(5);
    vi.unstubAllGlobals();
    fakeFetch({ ...table(), "/api/voice/calibration": { status: 422, json: { detail: { code: "too_little_voice" } } } });
    renderWithClient(<SettingsScreen session={SESSION_ACCOUNTS as AuthSession} />);
    await user.click((await screen.findAllByTestId("voice-check-start")).at(-1)!);
    // Lane HEAR2, decision 6: the reason in plain words, with what to do.
    expect(await screen.findByText("목소리가 잘 안 들렸어요. 조용한 곳에서 문장을 끝까지 읽어 주세요.")).toBeInTheDocument();
    engine.recordVoice.mockImplementation(async () => { throw Object.assign(new Error("no"), { name: "NotAllowedError" }); });
  });

  it("목소리 맞추기 names a short recording and a server that could not keep it (lane HEAR2, decision 6)", async () => {
    const user = userEvent.setup();
    engine.recordVoice.mockImplementation(async () => ({ pcm: new Int16Array(16000), micId: "mic-0011aabb" }));
    fakeFetch({ ...table(), "/api/voice/calibration": { status: 422, json: { detail: { code: "audio_length" } } } });
    renderWithClient(<SettingsScreen session={SESSION_ACCOUNTS as AuthSession} />);
    await user.click(await screen.findByTestId("voice-check-start"));
    expect(await screen.findByText("녹음이 너무 짧았어요. 문장을 끝까지 읽어 주세요.")).toBeInTheDocument();
    vi.unstubAllGlobals();
    // The person's test of 2026-10-26: the server answered 500 ten times and the screen said only "맞추지 못했어요".
    fakeFetch({ ...table(), "/api/voice/calibration": { status: 500, json: { detail: "workspace_error" } } });
    renderWithClient(<SettingsScreen session={SESSION_ACCOUNTS as AuthSession} />);
    await user.click((await screen.findAllByTestId("voice-check-start")).at(-1)!);
    expect(await screen.findByText("지금은 맞출 수 없어요. 잠시 뒤 다시 해 주세요. 계속되면 관리자에게 알려 주세요.")).toBeInTheDocument();
    engine.recordVoice.mockImplementation(async () => { throw Object.assign(new Error("no"), { name: "NotAllowedError" }); });
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
  it("reads the server's voice_reference and its 14 days", () => {
    const reference = parseVoiceReference("v1:-23.4:1761440000:mic-0011aabb");
    expect(reference).toEqual({ levelDb: -23.4, measuredAt: 1761440000000, micId: "mic-0011aabb" });
    expect(parseVoiceReference("none")).toBeNull();
    expect(referenceStale(reference!, 1761440000000 + 13 * 86400000)).toBe(false);
    expect(referenceStale(reference!, 1761440000000 + 15 * 86400000)).toBe(true);
  });

  it("names the page's address when the microphone needs HTTPS", () => {
    expect(micFailureWords(new Error("이 주소에서는 마이크를 쓸 수 없어요. HTTPS 주소로 열어 주세요."), "막힘", "실패")).toContain("HTTPS");
    expect(micFailureWords(Object.assign(new Error("x"), { name: "NotFoundError" }), "막힘", "실패")).toBe("실패");
  });
});
