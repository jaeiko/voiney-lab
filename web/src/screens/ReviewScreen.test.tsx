import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AuthSession } from "../api/types";
import { CARD_CLAIM, CARD_EVIDENCE, fakeFetch, renderWithClient, REVIEW_DRAFT, SESSION_ACCOUNTS, SESSION_DEV, SOURCE_PAGE_1 } from "../test/helpers";
import { ReviewScreen } from "./ReviewScreen";

const session = SESSION_ACCOUNTS as AuthSession;
const PAGE_2 = { ...SOURCE_PAGE_1, page: 2, lines: [{ number: 1, text: "Section wash", box: [72, 52, 150, 64] }, { number: 2, text: "3. Wash the pellet twice.", box: [72, 66, 200, 78] }, { number: 3, text: "4. Spin at 500 x g for 5 min.", box: [72, 80, 230, 92] }] };

afterEach(() => {
  vi.unstubAllGlobals();
});

function draftTable(extra: Record<string, unknown> = {}) {
  return {
    "/api/protocols/p-review/review-draft": { json: REVIEW_DRAFT },
    "/api/protocols/p-review/review-draft/source-pages/1": { json: SOURCE_PAGE_1 },
    "/api/protocols/p-review/review-draft/source-pages/2": { json: PAGE_2 },
    "/api/protocols": { json: { protocols: [] } },
    ...extra,
  };
}

describe("ReviewScreen", () => {
  it("loading, then the first card beside its source page with the band and the progress", async () => {
    fakeFetch(draftTable());
    const { container } = renderWithClient(<ReviewScreen protocolId="p-review" session={session} />);
    expect(screen.getByRole("status")).toHaveTextContent("확인할 것을 불러오는 중…");
    expect(await screen.findByTestId("review-progress")).toHaveTextContent("확인할 것 0 / 2");
    expect(screen.getByText("분석이 원문에서 확인하지 못한 값만 원문 옆에서 하나씩 정해요.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: CARD_EVIDENCE.question_ko })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "원문 1쪽" })).toHaveAttribute("src", "/api/protocols/p-review/review-draft/source-pages/1/image");
    expect(screen.getByRole("link", { name: "원문 열기" })).toHaveAttribute("href", "/api/protocols/p-review/review-draft/source-pages/1/image");
    // The source is not listed whole: only the page image and, for a card that needs lines, the numbered lines to pick.
    expect(await screen.findByTestId("review-lines")).toBeInTheDocument();
    expect(container.querySelector('.review-line[data-suggested="true"]')).toHaveTextContent("2. Incubate for 10 min at 37 C.");
    expect(screen.getByTestId("review-later")).toHaveAttribute("href", "#/protocols");
    expect(screen.getByTestId("review-prev")).toBeDisabled();
  });

  it("a line button needs picked lines; a tap picks them and the decision carries them; the next card follows; the end registers and offers the start", async () => {
    const user = userEvent.setup();
    const decided = { ...REVIEW_DRAFT, cards: [{ ...CARD_EVIDENCE, decided: true, decision: { action: "confirm", source_lines: { page: 1, first_line: 4, last_line: 4 } } }, CARD_CLAIM], next_card: 1 };
    const allDone = { ...decided, cards: [decided.cards[0], { ...CARD_CLAIM, decided: true, decision: { action: "confirm" } }], next_card: null, registrable: true };
    let decisions = 0;
    const { calls } = fakeFetch(
      draftTable({
        "/api/protocols/p-review/review-decisions": () => {
          decisions += 1;
          return { json: decisions === 1 ? decided : allDone };
        },
        "/api/protocols/p-review/review-versions": { status: 201, json: { version: 1 } },
      }),
    );
    renderWithClient(<ReviewScreen protocolId="p-review" session={session} />);
    await screen.findByTestId("review-lines");
    await user.click(screen.getByRole("button", { name: "고른 줄이 맞아요" }));
    expect(await screen.findByTestId("card-note")).toHaveTextContent("먼저 원문의 줄을 골라 주세요.");
    await user.click(screen.getByRole("button", { name: /^4/ }));
    expect(screen.getByText("고른 줄 · 1쪽 4줄")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "고른 줄이 맞아요" }));
    expect(await screen.findByRole("heading", { level: 2, name: CARD_CLAIM.question_ko })).toBeInTheDocument();
    const sent = calls.filter((call) => call.path === "/api/protocols/p-review/review-decisions");
    expect(JSON.parse(String(sent[0].init?.body))).toEqual({ item_id: "step:s2", action: "confirm", target: REVIEW_DRAFT.target, source_lines: { page: 1, first_line: 4, last_line: 4 } });
    expect(screen.getByTestId("review-progress")).toHaveTextContent("확인할 것 1 / 2");
    expect(screen.getByRole("img", { name: "원문 2쪽" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "맞아요" }));
    expect(await screen.findByTestId("review-done")).toHaveTextContent("확인 끝 — 이제 실험할 수 있어요");
    const registered = calls.find((call) => call.path === "/api/protocols/p-review/review-versions");
    expect(JSON.parse(String(registered?.init?.body))).toEqual({ target: REVIEW_DRAFT.target });
    // Lane RX2: the start goes to the new screen's start flow (startHref).
    expect(screen.getByTestId("review-start")).toHaveAttribute("href", "#/start/p-review");
  });

  it("a button that needs words opens the field, and 다시 분석 goes to the progress screen", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch(
      draftTable({
        "/api/protocols/p-review/review-draft": { json: { ...REVIEW_DRAFT, cards: [CARD_CLAIM, CARD_EVIDENCE], next_card: 0 } },
        "/api/protocols/p-review/review-decisions": { json: { ...REVIEW_DRAFT, cards: [{ ...CARD_CLAIM, decided: true, decision: { action: "fix", value: { text: "600 x g" } } }, CARD_EVIDENCE], next_card: 1 } },
        "/api/protocols/p-review/reanalysis": { status: 202, json: { analysis_request_accepted: true } },
      }),
    );
    renderWithClient(<ReviewScreen protocolId="p-review" session={session} />);
    await screen.findByTestId("review-progress");
    await user.click(screen.getByRole("button", { name: "직접 고칠게요" }));
    await user.type(screen.getByLabelText("고친 값"), "600 x g");
    await user.click(screen.getByRole("button", { name: "보내기" }));
    expect(await screen.findByRole("heading", { level: 2, name: CARD_EVIDENCE.question_ko })).toBeInTheDocument();
    const sent = calls.find((call) => call.path === "/api/protocols/p-review/review-decisions");
    expect(JSON.parse(String(sent?.init?.body))).toMatchObject({ item_id: "value:s4/quantities/0", action: "fix", text: "600 x g" });
    await user.click(screen.getByRole("button", { name: "다시 분석" }));
    await waitFor(() => expect(window.location.hash).toBe("#/protocols/p-review/analysis"));
    expect(calls.some((call) => call.path === "/api/protocols/p-review/reanalysis")).toBe(true);
  });

  it("in dev mode the registration asks for the person's name once", async () => {
    const user = userEvent.setup();
    let versions = 0;
    const done = { ...REVIEW_DRAFT, cards: [{ ...CARD_EVIDENCE, decided: true, decision: { action: "confirm" } }, { ...CARD_CLAIM, decided: true, decision: { action: "confirm" } }], next_card: null, registrable: true };
    const { calls } = fakeFetch(
      draftTable({
        "/api/protocols/p-review/review-draft": { json: done },
        "/api/protocols/p-review/review-versions": () => {
          versions += 1;
          return versions === 1 ? { status: 422, json: { detail: "reviewer_name_required" } } : { status: 201, json: { version: 1 } };
        },
      }),
    );
    renderWithClient(<ReviewScreen protocolId="p-review" session={SESSION_DEV as AuthSession} />);
    await user.type(await screen.findByTestId("reviewer-name"), "재준");
    await user.click(screen.getByRole("button", { name: "실험 시작" }));
    expect(await screen.findByTestId("review-done")).toBeInTheDocument();
    const second = calls.filter((call) => call.path === "/api/protocols/p-review/review-versions")[1];
    expect(JSON.parse(String(second.init?.body))).toEqual({ target: REVIEW_DRAFT.target, reviewer_name: "재준" });
  });

  it("a registered version read back is not registered again: the done words and 실험 시작", async () => {
    const { calls } = fakeFetch({
      "/api/protocols/p-review/review-draft": {
        json: { ...REVIEW_DRAFT, review_pending: null, available_for_execution: true, target: { kind: "review_version", version: 1 }, cards: [{ ...CARD_EVIDENCE, decided: true, decision: { action: "confirm" } }, { ...CARD_CLAIM, decided: true, decision: { action: "confirm" } }], next_card: null, registrable: true },
      },
    });
    renderWithClient(<ReviewScreen protocolId="p-review" session={session} />);
    expect(await screen.findByTestId("review-done")).toHaveTextContent("확인 끝 — 이제 실험할 수 있어요");
    expect(calls.some((call) => call.path.endsWith("/review-versions"))).toBe(false);
  });

  it("nothing to confirm: the sentence and 실험 시작; no analysis yet: the sentence alone", async () => {
    fakeFetch({ "/api/protocols/p-ready/review-draft": { json: { ...REVIEW_DRAFT, protocol_id: "p-ready", title: "In-gel digestion", available_for_execution: true, target: { kind: "analysis" }, cards: [], next_card: null } } });
    renderWithClient(<ReviewScreen protocolId="p-ready" session={session} />);
    expect(await screen.findByTestId("review-done")).toHaveTextContent("확인할 것이 없어요. 바로 시작할 수 있어요.");
    expect(within(screen.getByTestId("review-done")).getByRole("link", { name: "실험 시작" })).toBeInTheDocument();
    vi.unstubAllGlobals();
    fakeFetch({ "/api/protocols/p-new/review-draft": { json: { ...REVIEW_DRAFT, protocol_id: "p-new", title: "표지 최종 하리", available_for_execution: false, target: null, cards: [], message_ko: "검토할 분석이 아직 없습니다." } } });
    renderWithClient(<ReviewScreen protocolId="p-new" session={session} />);
    expect(await screen.findByText("아직 확인할 분석이 없어요. 분석이 끝나면 여기에 나와요.")).toBeInTheDocument();
  });

  it("error state: the sentence and 다시 불러오기", async () => {
    fakeFetch({ "/api/protocols/p-review/review-draft": new TypeError("Failed to fetch") });
    renderWithClient(<ReviewScreen protocolId="p-review" session={session} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("확인할 것을 불러오지 못했어요.");
  });
});
