import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PagePicker, pickPage } from "./PagePicker";

describe("pickPage", () => {
  it("the first tap is the start page, the second the end page in either order, a third starts over", () => {
    const empty = { first: null, last: null };
    expect(pickPage(empty, 3)).toEqual({ first: 3, last: null });
    expect(pickPage({ first: 3, last: null }, 5)).toEqual({ first: 3, last: 5 });
    expect(pickPage({ first: 5, last: null }, 3)).toEqual({ first: 3, last: 5 });
    expect(pickPage({ first: 3, last: null }, 3)).toEqual({ first: 3, last: 3 });
    expect(pickPage({ first: 3, last: 5 }, 2)).toEqual({ first: 2, last: null });
  });
});

describe("PagePicker", () => {
  it("shows a small picture per page, asks for the start then the end, and offers those pages only", async () => {
    const user = userEvent.setup();
    const onChoose = vi.fn();
    render(<PagePicker protocolId="p-new" pageCount={4} onChoose={onChoose} />);
    const thumbs = screen.getAllByRole("button", { name: /쪽$/ });
    expect(thumbs).toHaveLength(4);
    expect(thumbs[0].querySelector("img")).toHaveAttribute("src", "/api/protocols/p-new/review-draft/source-pages/1/image?size=thumb");
    expect(screen.getByTestId("page-picker-hint")).toHaveTextContent("시작 쪽을 눌러 주세요.");
    expect(screen.getByTestId("pages-start")).toBeDisabled();
    await user.click(thumbs[2]);
    expect(screen.getByTestId("page-picker-hint")).toHaveTextContent("끝 쪽을 눌러 주세요.");
    await user.click(thumbs[3]);
    expect(screen.getByTestId("page-picker-hint")).toHaveTextContent("3–4쪽을 분석해요");
    expect(thumbs[2]).toHaveAttribute("aria-pressed", "true");
    expect(thumbs[3]).toHaveAttribute("aria-pressed", "true");
    expect(thumbs[0]).toHaveAttribute("aria-pressed", "false");
    await user.click(screen.getByTestId("pages-start"));
    expect(onChoose).toHaveBeenCalledWith(3, 4);
  });
});
