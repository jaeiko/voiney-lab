import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fakeFetch, renderWithClient } from "../test/helpers";
import { EMPTY_RUN, type ProtocolState } from "../voice/runModel";
import { runStore } from "../voice/runStore";
import { recordLines } from "./run/RecordsPanel";
import { RunScreen } from "./RunScreen";

const engine = vi.hoisted(() => ({
  loadVoiceScripts: vi.fn(async () => undefined),
  togglePause: vi.fn(() => true),
  stopRun: vi.fn(async () => undefined),
  chooseCandidate: vi.fn(() => true),
  resumeSound: vi.fn(),
  reconnectMicrophone: vi.fn(),
  serverNow: () => Date.now(),
}));
vi.mock("../voice/engine", () => engine);

function protocol(over: Partial<ProtocolState> = {}): ProtocolState {
  return {
    attached: true, protocol_id: "p1", revision_id: "r1", display_name: "In-gel digestion", development_only: false, active: true, total_steps: 9,
    at_final_step: false, current_step_label: "4", current_step_id: "step-4", primary_summary: "젤 조각을 37 °C에서 10분 동안 둡니다.",
    display_summary: "4. Incubate the gel pieces at 37 °C for 10 min.", workflow_status: "running", block_reason: null, revision: 1, timers: { step: null }, ...over,
  };
}

function running(over: Partial<ProtocolState> = {}, model = {}) {
  runStore.reset({ phase: "running", active: true, voice: "LISTENING", experimentId: "s1", model: { ...EMPTY_RUN, protocol: protocol(over), stateRevision: 1, ...model } });
}

beforeEach(() => {
  fakeFetch({ "/api/workspace/experiments/s1/timeline": { json: { timeline: [{ event_id: "e1", event_type: "observation_recorded", step_label: "3", observation: { content: "투명해졌어요", capture_source: "voice" } }] } } });
});
afterEach(() => {
  act(() => runStore.reset());
  vi.unstubAllGlobals();
});

describe("RunScreen", () => {
  it("with no experiment there is no run bar, one way to start", () => {
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-empty")).toHaveTextContent("지금 하는 실험이 없어요.");
    expect(screen.queryByTestId("run-pause")).toBeNull();
    expect(screen.getByRole("link", { name: "실험 시작하기" })).toHaveAttribute("href", "#/start");
  });

  it("shows the step large with the source folded, the listening word, and no timer when the step has none", async () => {
    running();
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-step-text")).toHaveTextContent("젤 조각을 37 °C에서 10분 동안 둡니다.");
    expect(screen.getByText("원문 보기").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByTestId("run-listening")).toHaveTextContent("듣는 중");
    expect(screen.queryByTestId("run-timer")).toBeNull();
    expect(await screen.findByText("투명해졌어요")).toBeInTheDocument();
    expect(screen.getByTestId("record-memo")).toBeEnabled();
    expect(screen.getByTestId("record-photo")).toBeEnabled();
  });

  it("makes the timer a chip far from its end and large in its last minute", () => {
    const deadline = (seconds: number) => new Date(Date.now() + seconds * 1000).toISOString();
    running({ timers: { step: { state: "running", duration_seconds: 600, deadline_at: deadline(300), name: "10분" } } });
    const { unmount } = renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-timer")).toHaveAttribute("data-big", "false");
    unmount();
    running({ timers: { step: { state: "running", duration_seconds: 600, deadline_at: deadline(40), name: "10분" } } });
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-timer")).toHaveAttribute("data-big", "true");
  });

  it("while paused: the band across the screen, the word 일시정지, [다시 시작] resumes", async () => {
    const user = userEvent.setup();
    running({ workflow_status: "paused" });
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-paused")).toHaveTextContent("일시정지 중 — '다시 시작'이라고 말하거나 [다시 시작]을 누르세요");
    expect(screen.getByTestId("run-listening")).toHaveTextContent("일시정지");
    await user.click(screen.getByTestId("run-resume"));
    expect(engine.togglePause).toHaveBeenCalledWith(true);
  });

  it("says a word the server dropped, and asks before ending", async () => {
    const user = userEvent.setup();
    running({}, { misheardAt: Date.now() });
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-listening")).toHaveTextContent("잘 안 들렸어요");
    await user.click(screen.getByTestId("run-stop"));
    expect(screen.getByRole("dialog")).toHaveTextContent("실험을 끝낼까요?");
    await user.click(screen.getByTestId("run-stop-confirm"));
    expect(engine.stopRun).toHaveBeenCalled();
  });

  it("shows a timer paused by voice as paused, with its remaining time (lane HEAR2, decision 3)", () => {
    running({ timers: { step: { state: "paused", duration_seconds: 600, deadline_at: null, remaining_seconds: 540, name: "10분" } } });
    renderWithClient(<RunScreen />);
    const timer = screen.getByTestId("run-timer");
    expect(timer.getAttribute("data-kind")).toBe("paused");
    expect(timer).toHaveTextContent("9:00");
    expect(timer).toHaveTextContent("일시정지");
  });

  it("shows the server's reason for a dropped sound, and a provider that failed, in the listening band (lane HEAR2, decision 4)", () => {
    running({}, { misheardAt: Date.now(), misheard: { notice: null, reason: "너무 짧은 소리였어요" } });
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-listening")).toHaveTextContent("잘 안 들렸어요");
    expect(screen.getByTestId("run-listening")).toHaveTextContent("너무 짧은 소리였어요");
    expect(screen.getByTestId("run-listening")).not.toHaveTextContent("한 번 더 말해 주세요");
  });

  it("names a speech-to-text provider that failed where the dropped word goes (lane HEAR2, decisions 1-2)", () => {
    running({}, { misheardAt: Date.now(), misheard: { notice: "받아쓰기 서비스가 답하지 않아요", reason: "받아쓰기 서비스의 한도가 다 됐어요" } });
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-listening")).toHaveTextContent("받아쓰기 서비스가 답하지 않아요");
    expect(screen.getByTestId("run-listening")).toHaveTextContent("받아쓰기 서비스의 한도가 다 됐어요");
    expect(screen.getByTestId("run-listening").getAttribute("data-word")).toBe("misheard");
  });

  it("shows what the server said of the side-voice filter for a few seconds", () => {
    running({}, { filterNotice: { text: "마이크가 바뀌어 모든 말을 들어요 · 이 마이크로 다시 맞춰 주세요", at: Date.now() } });
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-filter-notice")).toHaveTextContent("이 마이크로 다시 맞춰 주세요");
  });

  it("names the step to go back to when the server refused the start", () => {
    runStore.reset({ startedProtocolId: "p1", startRefusal: { reason: "safety_acknowledgement_required", words: "안전 주의를 먼저 읽어 주세요." } });
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-refused")).toHaveTextContent("안전 주의를 먼저 읽어 주세요.");
    expect(screen.getByRole("link", { name: "실험 시작하기" })).toHaveAttribute("href", "#/start/p1?step=3");
  });

  it("shows the end with the notebook and home (lane RX4: the end screen)", () => {
    runStore.reset({ experimentId: "s1", model: { ...EMPTY_RUN, ended: { status: "completed", report: "saved" } } });
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-ended")).toHaveTextContent("실험을 마쳤어요");
    expect(screen.getByRole("link", { name: "실험 노트 보기" })).toHaveAttribute("href", "/index.html#/records/s1");
    expect(screen.getByRole("link", { name: "홈으로 가기" })).toHaveAttribute("href", "#/home");
  });
});

describe("recordLines", () => {
  it("lists voice records, memos and photos newest first, with a correction and a retraction", () => {
    const lines = recordLines([
      { event_type: "session_started" },
      { event_id: "a", event_type: "observation_recorded", step_label: "2", observation: { content: "처음", current_content: "고친 값", capture_source: "voice", corrected: true } },
      { event_id: "b", event_type: "observation_recorded", step_label: "3", observation: { content: "메모", capture_source: "manual", retracted: true } },
      { event_id: "c", event_type: "evidence_attached", step_label: "3", evidence: { original_filename: "gel.png" } },
    ]);
    expect(lines.map((line) => [line.kind, line.text, line.state])).toEqual([
      ["photo", "gel.png", null],
      ["memo", "메모", "retracted"],
      ["voice", "고친 값", "corrected"],
    ]);
  });
});

// --- Lane RX4 (2026-10-26): the pause that always goes on or ends, Korean first on the step ---
describe("RunScreen (lane RX4)", () => {
  it("while paused the band says the server's refusal and offers 종료, which asks once and stops", async () => {
    const user = userEvent.setup();
    running({ workflow_status: "paused" }, { notice: "현재 연결의 설정으로 다시 요청해 주세요." });
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-paused-note")).toHaveTextContent("현재 연결의 설정으로 다시 요청해 주세요.");
    expect(screen.getByTestId("run-resume")).toBeEnabled();
    await user.click(screen.getByTestId("run-paused-stop"));
    expect(screen.getByRole("dialog")).toHaveTextContent("실험을 끝낼까요?");
    await user.click(screen.getByTestId("run-stop-confirm"));
    expect(engine.stopRun).toHaveBeenCalled();
  });

  it("shows the Korean without the step's own label and folds the source; the next step without its label too", () => {
    running({ primary_summary: "4단계: 젤 조각을 37 °C에서 10분 동안 둡니다." }, { screen: { next_step_sentence: "5단계: 상층액을 버립니다." } });
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-step-text")).toHaveTextContent(/^젤 조각을 37 °C에서 10분 동안 둡니다\.$/);
    expect(screen.getByTestId("run-step-text")).toHaveAttribute("data-lang", "ko");
    expect(screen.getByTestId("run-source-text")).toHaveTextContent("4. Incubate the gel pieces at 37 °C for 10 min.");
    expect(screen.getByTestId("run-next")).toHaveTextContent("다음 단계: 상층액을 버립니다.");
    expect(screen.queryByTestId("run-step-pending")).toBeNull();
  });

  it("a step with no Korean yet says 번역 준비 중 and shows its source at an ordinary size, even with no step number", () => {
    const source = "Transfer analytical portion into a digestion vessel liner (avoid placing material on liner walls).";
    running({ current_step_label: "", primary_summary: source, display_summary: source }, { screen: { next_step_sentence: "Optional - Add 1 mL water." } });
    renderWithClient(<RunScreen />);
    expect(screen.getByTestId("run-step-pending")).toHaveTextContent("번역 준비 중");
    expect(screen.getByTestId("run-step-text")).toHaveAttribute("data-lang", "source");
    expect(screen.getByTestId("run-step-text")).toHaveTextContent(source);
    expect(screen.queryByTestId("run-source")).toBeNull();
    expect(screen.getByText("전체 9단계")).toBeInTheDocument();
    expect(screen.getByTestId("run-next")).toHaveAttribute("data-lang", "source");
    expect(screen.getByTestId("run-next")).toHaveTextContent("다음 단계 · 번역 준비 중");
  });
});
