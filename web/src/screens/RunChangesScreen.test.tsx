import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ENTRY_READY, fakeFetch, renderWithClient, RUN_OPTIONS } from "../test/helpers";
import { RunChangesScreen } from "./RunChangesScreen";

afterEach(() => {
  vi.unstubAllGlobals();
});

function table(extra: Record<string, unknown> = {}) {
  return {
    "/api/protocols/p-ready": { json: { ...ENTRY_READY, analysis_run: { state: "complete" } } },
    "/api/protocols/p-ready/start-check/changes": (init?: RequestInit) => (init?.method === "POST" ? { json: { change_set_id: "run-changes-1", changes: JSON.parse(String(init.body)).changes.map((item: { item_id: string }) => ({ item_id: item.item_id, step_label: "2", kind: "time", kind_ko: "시간", source_number: "10", source_unit: "min", number: "20", unit: "min", shown_ko: "20분", source_shown_ko: "10분", words_ko: "20분(오늘 바꾼 값 · 원문 10분)" })), source: "wizard" } } : { json: RUN_OPTIONS }),
    ...extra,
  };
}

describe("RunChangesScreen", () => {
  it("the usual case is one big button: 바꿀 값 없음 sends an empty choice and the start follows", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch(table());
    renderWithClient(<RunChangesScreen protocolId="p-ready" />);
    expect(screen.getByText("오늘 다르게 할 값을 미리 적어 두면 안내와 타이머가 그 값으로 가요.")).toBeInTheDocument();
    await user.click(await screen.findByTestId("changes-none"));
    expect(await screen.findByTestId("changes-chosen")).toHaveTextContent("원문대로 진행해요.");
    const sent = calls.find((call) => call.path === "/api/protocols/p-ready/start-check/changes" && call.init?.method === "POST");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({ target: RUN_OPTIONS.target, source: "wizard", changes: [] });
    expect(screen.getByTestId("changes-start")).toHaveAttribute("href", "/index.html#/protocols/p-ready");
  });

  it("바꿀 값이 있어요: the steps, the step's Korean sentence with the source folded, a marked value, number + unit, the comparison, the list and 다 골랐어요", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch(table());
    renderWithClient(<RunChangesScreen protocolId="p-ready" />);
    await user.click(await screen.findByTestId("changes-some"));
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(4);
    expect(within(rows[1]).getByText("바꿀 수 있는 값 2개")).toBeInTheDocument();
    expect(within(rows[2]).getByText("바꿀 값 없음")).toBeInTheDocument();
    await user.click(within(rows[1]).getByRole("button", { name: "2단계" }));
    expect(screen.getByTestId("step-korean")).toHaveTextContent("2. 37 °C에서 10분 동안 둡니다.");
    expect(screen.queryByTestId("step-source")).toBeNull();
    await user.click(screen.getByTestId("step-source-toggle"));
    const source = screen.getByTestId("step-source");
    expect(source).toHaveTextContent("2. Incubate for 10 min at 37 C.");
    expect(within(source).getByRole("button", { name: "10 min" })).toBeInTheDocument();
    await user.click(within(source).getByRole("button", { name: "10 min" }));
    await user.type(screen.getByTestId("value-number"), "20");
    expect(screen.getByRole("button", { name: "분" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("value-compare")).toHaveTextContent("원문: 10분 → 오늘: 20분");
    await user.click(screen.getByRole("button", { name: "시간" }));
    expect(screen.getByTestId("value-compare")).toHaveTextContent("원문: 10분 → 오늘: 20시간");
    await user.click(screen.getByTestId("value-use"));
    expect(screen.getByText("고른 값 1개")).toBeInTheDocument();
    // Opened again, the value shows the number and unit as typed (20시간), not the source unit.
    await user.click(within(screen.getByTestId("step-values")).getByRole("button", { name: "시간 10분" }));
    expect(screen.getByTestId("value-number")).toHaveValue("20");
    expect(screen.getByRole("button", { name: "시간" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "분" }));
    await user.click(screen.getByTestId("value-use"));
    expect(screen.getByText("고른 값 1개")).toBeInTheDocument();
    expect(screen.getByText(/2단계 시간 · 원문: 10분 → 오늘: 20분/)).toBeInTheDocument();
    await user.click(screen.getByTestId("changes-done"));
    expect(await screen.findByTestId("changes-chosen")).toHaveTextContent("오늘 바꾼 값 1개로 진행해요.");
    const sent = calls.find((call) => call.path === "/api/protocols/p-ready/start-check/changes" && call.init?.method === "POST");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({ target: RUN_OPTIONS.target, source: "wizard", changes: [{ item_id: "s2#1", value: "20분" }] });
  });

  it("a large value: the server's question once more, then the same choice sent with confirm_large", async () => {
    const user = userEvent.setup();
    let posts = 0;
    const { calls } = fakeFetch(
      table({
        "/api/protocols/p-ready/start-check/changes": (init?: RequestInit) => {
          if (init?.method !== "POST") return { json: RUN_OPTIONS };
          posts += 1;
          if (posts === 1) {
            return { status: 422, json: { detail: { code: "run_change_large_unconfirmed", message_ko: "2단계 시간을 10분에서 30분으로 바꿀까요? 원문 값과 많이 달라요.", large: [{ item_id: "s2#1", question_ko: "2단계 시간을 10분에서 30분으로 바꿀까요? 원문 값과 많이 달라요." }] } } };
          }
          return { json: { change_set_id: "run-changes-2", changes: [{ item_id: "s2#1", step_label: "2", kind: "time", kind_ko: "시간", source_number: "10", source_unit: "min", number: "30", unit: "min", shown_ko: "30분", words_ko: "30분(오늘 바꾼 값 · 원문 10분)" }], source: "wizard" } };
        },
      }),
    );
    renderWithClient(<RunChangesScreen protocolId="p-ready" />);
    await user.click(await screen.findByTestId("changes-some"));
    await user.click(screen.getByRole("button", { name: "2단계" }));
    await user.click(screen.getByRole("button", { name: "시간 10분" }));
    await user.type(screen.getByTestId("value-number"), "30");
    await user.click(screen.getByTestId("value-use"));
    await user.click(screen.getByTestId("changes-done"));
    expect(await screen.findByTestId("large-question")).toHaveTextContent("2단계 시간을 10분에서 30분으로 바꿀까요? 원문 값과 많이 달라요.");
    await user.click(screen.getByTestId("large-confirm"));
    expect(await screen.findByTestId("changes-chosen")).toHaveTextContent("오늘 바꾼 값 1개로 진행해요.");
    const second = calls.filter((call) => call.path === "/api/protocols/p-ready/start-check/changes" && call.init?.method === "POST")[1];
    expect(JSON.parse(String(second.init?.body))).toEqual({ target: RUN_OPTIONS.target, source: "wizard", changes: [{ item_id: "s2#1", value: "30분" }], confirm_large: ["s2#1"] });
  });

  it("지난번처럼 fills the list from the last run and sends source last_time", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch(
      table({
        "/api/protocols/p-ready/start-check/changes": (init?: RequestInit) =>
          init?.method === "POST"
            ? { json: { change_set_id: "run-changes-3", changes: [], source: "last_time" } }
            : { json: { ...RUN_OPTIONS, last_time: { changes: [{ item_id: "s4#2", step_label: "4", kind: "time", source_number: "5", source_unit: "min", number: "8", unit: "min", shown_ko: "8분" }], when_ko: "10월 20일" } } },
      }),
    );
    renderWithClient(<RunChangesScreen protocolId="p-ready" />);
    await user.click(await screen.findByTestId("changes-last-time"));
    expect(screen.getByText("고른 값 1개")).toBeInTheDocument();
    expect(screen.getByText(/4단계 시간 · 원문: 5분 → 오늘: 8분/)).toBeInTheDocument();
    await user.click(screen.getByTestId("changes-done"));
    await waitFor(() => expect(calls.some((call) => call.init?.method === "POST")).toBe(true));
    const sent = calls.find((call) => call.init?.method === "POST");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({ target: RUN_OPTIONS.target, source: "last_time", changes: [{ item_id: "s4#2", value: "8분" }] });
  });

  it("the server's refusal of a value is shown in its own words", async () => {
    const user = userEvent.setup();
    fakeFetch(
      table({
        "/api/protocols/p-ready/start-check/changes": (init?: RequestInit) =>
          init?.method === "POST" ? { status: 422, json: { detail: { code: "run_change_number_invalid", message_ko: "2단계 10분: 새 값을 숫자와 단위로 넣어 주세요(예: 20분)." } } } : { json: RUN_OPTIONS },
      }),
    );
    renderWithClient(<RunChangesScreen protocolId="p-ready" />);
    await user.click(await screen.findByTestId("changes-some"));
    await user.click(screen.getByRole("button", { name: "2단계" }));
    await user.click(screen.getByRole("button", { name: "시간 10분" }));
    await user.click(screen.getByRole("button", { name: "글로 쓰기" }));
    await user.type(screen.getByTestId("value-text"), "스무 분");
    await user.click(screen.getByTestId("value-use"));
    await user.click(screen.getByTestId("changes-done"));
    expect(await screen.findByRole("alert")).toHaveTextContent("2단계 10분: 새 값을 숫자와 단위로 넣어 주세요(예: 20분).");
  });
});
