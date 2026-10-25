import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fakeFetch, renderWithClient } from "../test/helpers";
import { TitleLine } from "./TitleLine";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TitleLine", () => {
  it("shows the file's name for a cover-line title, with the read title in small print", () => {
    fakeFetch({});
    renderWithClient(<TitleLine entry={{ protocol_id: "p-2", title: "표지 최종 하리", source_filename: "western-blot.pdf" }} />);
    expect(screen.getByTestId("protocol-title")).toHaveTextContent("western-blot");
    expect(screen.getByTestId("title-read-as")).toHaveTextContent("분석이 읽은 제목: 표지 최종 하리");
    expect(screen.getByRole("button", { name: "이름 바꾸기" })).toBeInTheDocument();
  });

  it("a real title stands as it is, and the development fixture offers no rename", () => {
    fakeFetch({});
    renderWithClient(<TitleLine entry={{ protocol_id: "p-1", title: "In-gel digestion", source_filename: "in-gel.pdf" }} renamable={false} />);
    expect(screen.getByTestId("protocol-title")).toHaveTextContent("In-gel digestion");
    expect(screen.queryByTestId("title-read-as")).toBeNull();
    expect(screen.queryByRole("button", { name: "이름 바꾸기" })).toBeNull();
  });

  it("renames through the server and reads the lists again; a refused name is said", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch({
      "/api/protocols/p-2/title": (init) => {
        const body = JSON.parse(String(init?.body ?? "{}"));
        return body.title === "x" ? { status: 400, json: { detail: "protocol_title_invalid" } } : { json: { title: body.title, protocol: {} } };
      },
      "/api/protocols": { json: { protocols: [] } },
    });
    const { client } = renderWithClient(<TitleLine entry={{ protocol_id: "p-2", title: "표지 최종 하리", source_filename: "western-blot.pdf" }} />);
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await user.click(screen.getByRole("button", { name: "이름 바꾸기" }));
    const field = screen.getByLabelText("프로토콜 이름");
    expect(field).toHaveValue("western-blot");
    await user.clear(field);
    await user.type(field, "x");
    await user.click(screen.getByRole("button", { name: "저장하기" }));
    expect(await screen.findByTestId("title-error")).toHaveTextContent("이름은 1–120자로 적어 주세요.");
    await user.clear(field);
    await user.type(field, "웨스턴 블롯");
    await user.click(screen.getByRole("button", { name: "저장하기" }));
    await waitFor(() => expect(screen.queryByTestId("title-edit")).toBeNull());
    const sent = calls.filter((call) => call.path === "/api/protocols/p-2/title");
    expect(JSON.parse(String(sent[1].init?.body))).toEqual({ title: "웨스턴 블롯" });
    expect(invalidate).toHaveBeenCalled();
  });
});
