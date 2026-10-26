import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ENTRY_READY, ENTRY_REVIEW, fakeFetch, renderWithClient, RUN_OPTIONS } from "../test/helpers";
import { StartScreen } from "./StartScreen";

const engine = vi.hoisted(() => ({ primeStart: vi.fn(), startRun: vi.fn(async () => undefined), loadVoiceScripts: vi.fn(async () => undefined) }));
vi.mock("../voice/engine", () => engine);

const TARGET = { kind: "analysis", analysis_id: "an-1" };
const CHECK = {
  enabled: true, target: TARGET, skipped_reason: null,
  questions: [{ question_id: "q1", question_ko: "2단계의 시간은 원문에 얼마라고 적혀 있나요?", source: { page: 1, excerpt: "2. Incubate for 10 min.", lines: { first_line: 4, last_line: 4 } }, choices: [{ choice_id: "c1", label_ko: "100 min" }, { choice_id: "c2", label_ko: "10 min" }] }],
};
const REVIEW = { protocol_id: "p-ready", safety_notices: [{ step_label: "2", source_page_number: 1, primary_text: "장갑을 끼세요.", source_text: "Wear gloves." }], execution_notices: [] };

function table(extra: Record<string, unknown> = {}) {
  return {
    "/api/protocols": { json: { protocols: [ENTRY_REVIEW, ENTRY_READY] } },
    "/api/protocols/p-ready/start-check": { json: CHECK },
    "/api/protocols/p-ready/start-check/answers": { json: { proceed: true } },
    "/api/protocols/p-ready/start-check/safety": { json: { ready: true } },
    "/api/protocols/p-ready/review": { json: REVIEW },
    "/api/protocols/p-ready/start-check/changes": (init?: RequestInit) => (init?.method === "POST" ? { json: { change_set_id: "run-changes-1", changes: [], source: "wizard" } } : { json: RUN_OPTIONS }),
    "/api/experimenter/settings": (init?: RequestInit) => ({ json: { settings: { ambient_mode: init?.method === "PUT" ? JSON.parse(String(init.body)).ambient_mode : "quiet", agent_voice: "default" } } }),
    "/api/voice/voices": { json: { default_voice_id: "v1", voices: [{ voice_id: "v1", label_ko: "차론 · 남성" }] } },
    ...extra,
  };
}

beforeEach(() => {
  window.location.hash = "#/start";
  engine.primeStart.mockClear();
  engine.startRun.mockClear();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StartScreen", () => {
  it("walks ① to ⑤ one screen at a time, each with its reason, and starts with the fixed name", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch(table());
    renderWithClient(<StartScreen protocolId={null} resumeId={null} step={null} />);
    expect(screen.getByTestId("start-why")).toHaveTextContent("어떤 원문으로 실험할지 확인해요.");
    // A protocol not ready cannot be picked; the ready one can.
    expect(await screen.findByRole("button", { name: /Western blot/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /In-gel digestion/ }));
    await user.click(screen.getByTestId("start-next"));
    expect(screen.getByTestId("start-why")).toHaveTextContent("원문에 적힌 값을 한 번 확인해요 — 잘못 분석된 값을 막으려고요.");
    await user.click(await screen.findByRole("button", { name: "10 min" }));
    expect(JSON.parse(String(calls.find((call) => call.path.endsWith("/answers"))?.init?.body))).toEqual({ question_id: "q1", choice_id: "c2" });
    expect(await screen.findByTestId("safety-list")).toHaveTextContent("장갑을 끼세요.");
    await user.click(screen.getByTestId("start-next"));
    expect(JSON.parse(String(calls.find((call) => call.path.endsWith("/safety"))?.init?.body))).toEqual({ target: TARGET });
    await user.click(await screen.findByTestId("changes-none"));
    expect(await screen.findByTestId("ambient-choice")).toHaveTextContent("시끄러우면 기록·단계 이동 전에 한 번 더 확인해요.");
    expect(screen.getByTestId("start-summary")).toHaveTextContent("안내 목소리 · 차론 · 남성");
    await user.click(screen.getByText("시끄러워요"));
    const go = screen.getByTestId("start-go");
    expect(go).toHaveTextContent("이 프로토콜로 시작");
    await user.click(go);
    await waitFor(() => expect(engine.startRun).toHaveBeenCalledWith({ protocolId: "p-ready", changeSetId: "run-changes-1", resume: null }));
    expect(engine.primeStart).toHaveBeenCalledTimes(1);
    expect(calls.some((call) => call.path === "/api/experimenter/settings" && call.init?.method === "PUT" && String(call.init.body).includes("noisy"))).toBe(true);
    expect(window.location.hash).toBe("#/experiment");
  });

  it("has no voice calibration step and a way back from every step", async () => {
    const user = userEvent.setup();
    fakeFetch(table());
    renderWithClient(<StartScreen protocolId="p-ready" resumeId={null} step={null} />);
    const steps = screen.getByTestId("step-progress");
    expect([...steps.querySelectorAll(".step-progress-name")].map((item) => item.textContent)).toEqual(["프로토콜 확인", "원문 값 확인", "안전 주의 읽기", "오늘 바꿀 값", "시작"]);
    expect(steps.textContent).not.toMatch(/목소리/);
    await user.click(await screen.findByTestId("start-next"));
    expect(await screen.findByTestId("check-progress")).toHaveTextContent("1개 중 1번째");
    await user.click(screen.getByTestId("start-back"));
    expect(screen.getByTestId("start-why")).toHaveTextContent("어떤 원문으로 실험할지 확인해요.");
  });

  it("says when the source has no numbers to check, and when the check would not load", async () => {
    const user = userEvent.setup();
    fakeFetch(table({ "/api/protocols/p-ready/start-check": { json: { enabled: true, questions: [], skipped_reason: "no_numeric_values" } } }));
    renderWithClient(<StartScreen protocolId="p-ready" resumeId={null} step={2} />);
    expect(await screen.findByText("원문에 확인할 숫자가 없어요. 다음으로 가 주세요.")).toBeInTheDocument();
    vi.unstubAllGlobals();
    fakeFetch(table({ "/api/protocols/p-ready/start-check": { status: 503, json: { detail: { code: "start_check_unavailable", message_ko: "핵심 값을 지금 읽을 수 없어요." } } } }));
    renderWithClient(<StartScreen protocolId="p-ready" resumeId={null} step={2} />);
    expect(await screen.findByText("핵심 값을 지금 읽을 수 없어요.")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "다시 불러오기" })[0]);
  });

  it("continues a saved experiment with its own start only", async () => {
    const user = userEvent.setup();
    fakeFetch(table({ "/api/workspace/experiments/s9": { json: { session_id: "s9", version: 4, protocol_id: "p-ready", current_step_label: "6" } } }));
    renderWithClient(<StartScreen protocolId="p-ready" resumeId="s9" step={null} />);
    expect(await screen.findByText("하던 실험을 6단계부터 이어서 해요.")).toBeInTheDocument();
    await user.click(screen.getByTestId("start-go"));
    await waitFor(() => expect(engine.startRun).toHaveBeenCalledWith({ protocolId: "p-ready", changeSetId: null, resume: { sessionId: "s9", version: 4 } }));
  });
});
