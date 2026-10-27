import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuthSession } from "../api/types";
import { ENTRY_ANALYSING, ENTRY_READY, ENTRY_REVIEW, fakeFetch, renderWithClient, SESSION_ACCOUNTS, SESSION_DEV } from "../test/helpers";
import { HomeScreen, situationOf } from "./HomeScreen";

afterEach(() => {
  vi.unstubAllGlobals();
});

const session = SESSION_ACCOUNTS as AuthSession;

describe("situationOf", () => {
  it("prefers my saved experiment, then a review, then the first upload, then analysing, then the start", () => {
    const mine = { session_id: "s1", protocol_id: "p-ready", status: "paused", current_step_label: "4", owner_principal_id: "me", started_at: "2026-10-24T01:00:00Z" };
    const theirs = { ...mine, session_id: "s2", owner_principal_id: "other" };
    expect(situationOf([ENTRY_READY], [mine], "me").kind).toBe("resume");
    expect(situationOf([ENTRY_READY], [theirs], "me").kind).toBe("start");
    expect(situationOf([ENTRY_READY, ENTRY_REVIEW], [], "me").kind).toBe("review");
    expect(situationOf([], [], "me").kind).toBe("first");
    expect(situationOf([ENTRY_ANALYSING], [], "me").kind).toBe("analysing");
    expect(situationOf([ENTRY_READY, ENTRY_ANALYSING], [], "me")).toEqual({ kind: "start", ready: 1 });
    expect(situationOf([ENTRY_READY], [{ ...mine, status: "completed" }], "me").kind).toBe("start");
  });
});

describe("HomeScreen", () => {
  it("loading state", () => {
    fakeFetch({ "/api/protocols": () => new Promise(() => {}) as never });
    renderWithClient(<HomeScreen session={session} />);
    expect(screen.getByRole("status")).toHaveTextContent("불러오는 중…");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("안녕하세요, 재준 님.");
  });

  it("empty state: one card, 첫 프로토콜 올리기, going to the upload screen", async () => {
    fakeFetch({ "/api/protocols": { json: { protocols: [] } }, "/api/workspace/experiments": { json: { experiments: [] } }, "/api/workspace/session": { json: { principal_id: "me" } } });
    renderWithClient(<HomeScreen session={session} />);
    const card = await screen.findByText("첫 프로토콜 올리기");
    expect(card.closest("a")).toHaveAttribute("href", "#/upload");
    expect(screen.getAllByRole("link").filter((link) => link.classList.contains("big-card"))).toHaveLength(1);
  });

  it("error state: the sentence and 다시 불러오기", async () => {
    fakeFetch({ "/api/protocols": { status: 503, json: { detail: "configuration_invalid" } } });
    renderWithClient(<HomeScreen session={session} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("프로토콜 목록을 불러오지 못했어요.");
    expect(screen.getByRole("button", { name: "다시 불러오기" })).toBeInTheDocument();
  });

  it("normal state: a saved experiment of mine becomes 이어서 하기 · N단계부터", async () => {
    fakeFetch({
      "/api/protocols": { json: { protocols: [ENTRY_READY] } },
      "/api/workspace/experiments": { json: { experiments: [{ session_id: "s1", protocol_id: "p-ready", protocol_title: "In-gel digestion", status: "in_progress", current_step_label: "4", owner_principal_id: "me", started_at: "2026-10-24T01:00:00Z" }] } },
      "/api/workspace/session": { json: { principal_id: "me" } },
    });
    renderWithClient(<HomeScreen session={session} />);
    expect(await screen.findByText("이어서 하기")).toBeInTheDocument();
    expect(screen.getByText("In-gel digestion · 4단계부터")).toBeInTheDocument();
    // Lane RX2: 이어서 하기 opens the new start flow's continuing start for that experiment.
    expect(screen.getByText("이어서 하기").closest("a")).toHaveAttribute("href", "#/start/p-ready?resume=s1");
  });

  it("normal state: a review-pending protocol becomes 확인하기 with the count", async () => {
    fakeFetch({ "/api/protocols": { json: { protocols: [ENTRY_READY, ENTRY_REVIEW] } }, "/api/workspace/experiments": { status: 503, json: { detail: "workspace_disabled" } }, "/api/workspace/session": { status: 503, json: { detail: "x" } } });
    renderWithClient(<HomeScreen session={session} />);
    expect(await screen.findByText("확인하기")).toBeInTheDocument();
    expect(screen.getByText("Western blot · 확인할 것 2개")).toBeInTheDocument();
    expect(screen.getByText("2가지만 확인하면 실험에 쓸 수 있어요.")).toBeInTheDocument();
    expect(screen.getByText("확인하기").closest("a")).toHaveAttribute("href", "#/protocols/p-review/review");
  });

  it("normal state: runnable protocols become 실험 시작 with the count and three small actions", async () => {
    fakeFetch({ "/api/protocols": { json: { protocols: [ENTRY_READY] } }, "/api/workspace/experiments": { json: { experiments: [] } }, "/api/workspace/session": { json: { principal_id: "me" } } });
    renderWithClient(<HomeScreen session={session} />);
    expect(await screen.findByText("실험 시작")).toBeInTheDocument();
    expect(screen.getByText("바로 쓸 수 있는 프로토콜 1개")).toBeInTheDocument();
    // Lane RX4: the small actions carry an icon beside the word, so the link is found by its role and name.
    expect(screen.getByRole("link", { name: "프로토콜 보기" })).toHaveAttribute("href", "#/protocols");
    expect(screen.getByRole("link", { name: "올리기" })).toHaveAttribute("href", "#/upload");
    expect(screen.getByRole("link", { name: "지난 기록" })).toHaveAttribute("href", "#/records");
  });

  it("normal state: only an analysis under way shows 분석 중 with the elapsed time, leading to its progress screen", async () => {
    vi.useFakeTimers({ now: Date.parse("2026-10-24T01:05:30Z"), toFake: ["Date"] });
    fakeFetch({
      "/api/protocols": { json: { protocols: [ENTRY_ANALYSING] } },
      "/api/protocols/p-analysing": { json: { ...ENTRY_ANALYSING, analysis_run: { state: "analyzing", requested_at: "2026-10-24T01:00:00Z" } } },
      "/api/workspace/experiments": { json: { experiments: [] } },
      "/api/workspace/session": { json: { principal_id: "me" } },
    });
    renderWithClient(<HomeScreen session={session} />);
    expect(await screen.findByText("분석 중")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("RNA extraction · 5분 지남")).toBeInTheDocument());
    expect(screen.getByText("분석 중").closest(".big-card")).toHaveAttribute("href", "#/protocols/p-analysing/analysis");
    vi.useRealTimers();
  });

  it("dev mode greets without a name", async () => {
    fakeFetch({ "/api/protocols": { json: { protocols: [] } }, "/api/workspace/experiments": { json: { experiments: [] } }, "/api/workspace/session": { json: { principal_id: "dev" } } });
    renderWithClient(<HomeScreen session={SESSION_DEV as AuthSession} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("안녕하세요.");
    await screen.findByText("첫 프로토콜 올리기");
  });
});
