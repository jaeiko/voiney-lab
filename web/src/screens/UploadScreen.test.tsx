import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthSession, UploadResponse } from "../api/types";
import { ApiError } from "../api/client";
import { ENTRY_READY, ENTRY_UPLOADED, fakeFetch, renderWithClient, SESSION_ACCOUNTS, SOURCE_PAGES, UPLOAD_ANSWER } from "../test/helpers";
import { afterUpload, UploadScreen, uploadErrorWords } from "./UploadScreen";

const session = SESSION_ACCOUNTS as AuthSession;

beforeEach(() => {
  window.location.hash = "";
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function pdf(name = "western-blot.pdf"): File {
  return new File([new Uint8Array([0x25, 0x50, 0x44, 0x46])], name, { type: "application/pdf" });
}

async function answerSource(user: ReturnType<typeof userEvent.setup>, note = "시판 제품 설명서") {
  await user.click(screen.getByRole("radio", { name: "예" }));
  await user.type(screen.getByLabelText("출처 메모"), note);
}

describe("afterUpload", () => {
  it("goes to the progress screen when the analysis ran, runs or will start by itself; else asks the pages", () => {
    expect(afterUpload(UPLOAD_ANSWER as UploadResponse)).toBe("pages");
    expect(afterUpload({ protocol: ENTRY_READY, deduplicated: true })).toBe("analysis");
    expect(afterUpload({ protocol: ENTRY_UPLOADED, ocr: { state: "queued", automatic: true } })).toBe("analysis");
    expect(afterUpload({ protocol: { ...ENTRY_UPLOADED, analysis_status: "analyzing" } })).toBe("analysis");
    expect(afterUpload({ protocol: { ...ENTRY_UPLOADED, analysis_status: "analysis_failed" } })).toBe("pages");
  });
});

describe("UploadScreen", () => {
  it("says why, offers one big [파일 고르기] and the link form", () => {
    fakeFetch({});
    renderWithClient(<UploadScreen session={session} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("프로토콜 올리기");
    expect(screen.getByText("원문을 올리면 값과 안전 주의를 그 원문에서만 읽어 안내해요.")).toBeInTheDocument();
    expect(screen.getByText("이 원문은 공개되었거나 시판 제품에 딸린 프로토콜인가요?")).toBeInTheDocument();
    expect(screen.getAllByRole("radio").map((radio) => radio.getAttribute("value"))).toEqual(["yes", "no", "unknown"]);
    expect(screen.getByTestId("file-pick")).toHaveTextContent("파일 고르기");
    expect(screen.getByRole("button", { name: "링크로 가져오기" })).toBeDisabled();
  });

  it("uploads the file, asks which pages, and starts the whole document", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch({
      "/api/protocols?filename=western-blot.pdf": { json: UPLOAD_ANSWER },
      "/api/protocols/p-new/analysis": { status: 202, json: { analysis_request_accepted: true } },
      "/api/protocols": { json: { protocols: [] } },
    });
    renderWithClient(<UploadScreen session={session} />);
    await answerSource(user, "제조사 공개 설명서");
    await user.upload(screen.getByTestId("file-input"), pdf());
    expect(await screen.findByTestId("pages-ask")).toBeInTheDocument();
    expect(screen.getByText("어느 쪽을 분석할까요?")).toBeInTheDocument();
    expect(screen.getByTestId("pages-all")).toHaveTextContent("전체 분석");
    expect(screen.getByTestId("pages-pick")).toHaveTextContent("쪽 고르기");
    const sent = calls.find((call) => call.path.startsWith("/api/protocols?filename="));
    expect(sent?.init?.method).toBe("POST");
    expect((sent?.init?.headers as Record<string, string>)["Content-Type"]).toBe("application/pdf");
    expect((sent?.init?.headers as Record<string, string>)["X-Voiney-Source-Status"]).toBe("yes");
    expect(decodeURIComponent((sent?.init?.headers as Record<string, string>)["X-Voiney-Source-Note"])).toBe("제조사 공개 설명서");
    await user.click(screen.getByTestId("pages-all"));
    await waitFor(() => expect(window.location.hash).toBe("#/protocols/p-new/analysis"));
    expect(calls.some((call) => call.path === "/api/protocols/p-new/analysis" && call.init?.method === "POST")).toBe(true);
  });

  it("pastes a Methods paragraph into the same upload and analysis path", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch({
      "/api/protocols?filename=pasted-methods.txt": { json: UPLOAD_ANSWER },
      "/api/protocols": { json: { protocols: [] } },
    });
    renderWithClient(<UploadScreen session={session} />);
    await answerSource(user, "CC BY paper DOI 10.0000/example");
    await user.type(
      screen.getByLabelText("Methods 문단"),
      "Cells were washed with 500 µL PBS, then incubated at 37 °C for 10 min.",
    );
    await user.click(screen.getByRole("button", { name: "Methods 문단 올리기" }));
    expect(await screen.findByTestId("pages-ask")).toBeInTheDocument();
    const sent = calls.find((call) => call.path === "/api/protocols?filename=pasted-methods.txt");
    expect(sent?.init?.method).toBe("POST");
    expect((sent?.init?.headers as Record<string, string>)["Content-Type"]).toBe("text/plain");
    expect(await (sent?.init?.body as Blob).text()).toBe(
      "Cells were washed with 500 µL PBS, then incubated at 37 °C for 10 min.",
    );
  });

  it("쪽 고르기 shows the pages' small pictures and analyses the picked pages only", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch({
      "/api/protocols?filename=western-blot.pdf": { json: UPLOAD_ANSWER },
      "/api/protocols/p-new/review-draft/source-pages": { json: SOURCE_PAGES },
      "/api/protocols/p-new/analysis": { status: 202, json: { analysis_request_accepted: true } },
      "/api/protocols": { json: { protocols: [] } },
    });
    renderWithClient(<UploadScreen session={session} />);
    await answerSource(user);
    await user.upload(screen.getByTestId("file-input"), pdf());
    await user.click(await screen.findByTestId("pages-pick"));
    expect(await screen.findByTestId("page-picker")).toBeInTheDocument();
    expect(screen.getByText("전체 4쪽")).toBeInTheDocument();
    const thumbs = screen.getAllByRole("button", { name: /쪽$/ });
    await user.click(thumbs[1]);
    await user.click(thumbs[2]);
    await user.click(screen.getByTestId("pages-start"));
    await waitFor(() => expect(window.location.hash).toBe("#/protocols/p-new/analysis"));
    expect(calls.some((call) => call.path === "/api/protocols/p-new/analysis?first_page=2&last_page=3")).toBe(true);
  });

  it("a file already analysed goes straight to its progress screen", async () => {
    const user = userEvent.setup();
    fakeFetch({ "/api/protocols?filename=in-gel.pdf": { json: { protocol: ENTRY_READY, deduplicated: true } }, "/api/protocols": { json: { protocols: [] } } });
    renderWithClient(<UploadScreen session={session} />);
    await answerSource(user);
    await user.upload(screen.getByTestId("file-input"), pdf("in-gel.pdf"));
    await waitFor(() => expect(window.location.hash).toBe("#/protocols/p-ready/analysis"));
  });

  it("a refused file is said in plain words and the picker stays", async () => {
    const user = userEvent.setup();
    fakeFetch({ "/api/protocols?filename=secret.pdf": { status: 422, json: { detail: "encrypted_pdf" } } });
    renderWithClient(<UploadScreen session={session} />);
    await answerSource(user);
    await user.upload(screen.getByTestId("file-input"), pdf("secret.pdf"));
    expect(await screen.findByTestId("upload-error")).toHaveTextContent("암호가 걸린 PDF는 올릴 수 없어요. 암호 없는 원본을 골라 주세요.");
    expect(screen.getByTestId("file-pick")).toBeInTheDocument();
    expect(uploadErrorWords(new ApiError(413, "pasted_methods_too_large"))).toBe("Methods 문단이 너무 길어요. 256 KiB 이하로 붙여 넣어 주세요.");
    expect(uploadErrorWords(new TypeError("x"))).toBe("올리지 못했어요. 다시 해 주세요.");
  });

  it("a link is sent to the server and its own refusal sentence is shown", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch({ "/api/protocols/import-link": { status: 422, json: { detail: { code: "link_private", message_ko: "공유 설정을 바꾸거나 파일을 내려받아 올려 주세요." } } } });
    renderWithClient(<UploadScreen session={session} />);
    await answerSource(user, "공개 Google Drive 원문");
    await user.type(screen.getByTestId("link-input"), "https://drive.google.com/file/d/abc/view");
    await user.click(screen.getByRole("button", { name: "링크로 가져오기" }));
    expect(await screen.findByTestId("upload-error")).toHaveTextContent("공유 설정을 바꾸거나 파일을 내려받아 올려 주세요.");
    const sent = calls.find((call) => call.path === "/api/protocols/import-link");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({ url: "https://drive.google.com/file/d/abc/view", public_source_status: "yes", source_note: "공개 Google Drive 원문" });
  });

  it("requires a source answer before it reads a chosen file", async () => {
    const user = userEvent.setup();
    const { calls } = fakeFetch({});
    renderWithClient(<UploadScreen session={session} />);
    await user.upload(screen.getByTestId("file-input"), pdf());
    expect(await screen.findByTestId("upload-error")).toHaveTextContent("예·아니요·모름 중 하나를 골라 주세요.");
    expect(calls).toHaveLength(0);
  });
});
