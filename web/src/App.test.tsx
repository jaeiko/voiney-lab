import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App, inviteTokenOf } from "./App";
import { parseRoute } from "./lib/navigation";
import { fakeFetch, renderWithClient, SESSION_ACCOUNTS, SESSION_DEV, SESSION_OUT } from "./test/helpers";

const replace = vi.fn();

beforeEach(() => {
  window.location.hash = "";
  vi.stubGlobal("location", { ...window.location, hash: "", search: "", pathname: "/app/", replace });
});
afterEach(() => {
  vi.unstubAllGlobals();
  replace.mockReset();
});

describe("routes", () => {
  it("knows the home, the list, the upload and the protocol screens; everything else is the old screen's", () => {
    expect(parseRoute("")).toEqual({ name: "home", hash: "#/home" });
    expect(parseRoute("#/home")).toEqual({ name: "home", hash: "#/home" });
    expect(parseRoute("#/protocols")).toEqual({ name: "protocols", hash: "#/protocols" });
    expect(parseRoute("#/protocols/p-1")).toEqual({ name: "handoff", hash: "#/protocols/p-1" });
    expect(parseRoute("#/upload")).toEqual({ name: "upload", hash: "#/upload" });
    expect(parseRoute("#/protocols/p-1/analysis")).toEqual({ name: "analysis", hash: "#/protocols/p-1/analysis", protocolId: "p-1" });
    expect(parseRoute("#/protocols/p%201/review")).toEqual({ name: "review", hash: "#/protocols/p%201/review", protocolId: "p 1" });
    expect(parseRoute("#/protocols/p-1/changes")).toEqual({ name: "changes", hash: "#/protocols/p-1/changes", protocolId: "p-1" });
    expect(parseRoute("#/protocols/p-1/other")).toEqual({ name: "handoff", hash: "#/protocols/p-1/other" });
    expect(parseRoute("#/records/s-1")).toEqual({ name: "handoff", hash: "#/records/s-1" });
    expect(parseRoute("#nonsense")).toEqual({ name: "home", hash: "#/home" });
    expect(inviteTokenOf("?invite=abc")).toBe("abc");
    expect(inviteTokenOf("")).toBeNull();
  });
});

describe("App", () => {
  it("lane RX2: the first guide comes once after a login, not in the development mode", async () => {
    localStorage.clear();
    fakeFetch({ "/api/auth/session": { json: SESSION_ACCOUNTS }, "/api/protocols": { json: { protocols: [] } }, "/api/workspace/experiments": { json: { experiments: [] } }, "/api/workspace/session": { json: { principal_id: "a1" } } });
    renderWithClient(<App />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("#/welcome"));
    localStorage.setItem("voiney-lab.app-welcome:a1", "done");
  });

  it("shows the login when accounts are on and nobody is signed in", async () => {
    fakeFetch({ "/api/auth/session": { json: SESSION_OUT } });
    renderWithClient(<App />);
    expect(screen.getByRole("status")).toHaveTextContent("확인하는 중…");
    expect(await screen.findByRole("button", { name: "로그인하기" })).toBeInTheDocument();
  });

  it("shows the frame with the account line, the menu and no experiment bar when signed in", async () => {
    fakeFetch({ "/api/auth/session": { json: SESSION_ACCOUNTS }, "/api/protocols": { json: { protocols: [] } }, "/api/workspace/experiments": { json: { experiments: [] } }, "/api/workspace/session": { json: { principal_id: "a1" } } });
    renderWithClient(<App />);
    expect(await screen.findByTestId("account-line")).toHaveTextContent("재준 · 연구실 lab-a");
    const nav = screen.getByRole("navigation", { name: "화면 고르기" });
    expect(nav.querySelector('[data-nav="home"]')).toHaveAttribute("aria-current", "page");
    // Lane RX2 (person's decisions 1-5, 2026-10-25): the experiment and the settings are the new screen's own now.
    expect(nav.querySelector('[data-nav="experiment"]')).toHaveAttribute("href", "#/experiment");
    expect(nav.querySelector('[data-nav="settings"]')).toHaveAttribute("href", "#/settings");
    for (const word of ["일시정지", "종료", "00:00", "안전 자료 확인 중", "프로토콜을 고르세요", "확인 필요"]) expect(screen.queryByText(word)).toBeNull();
  });

  it("dev mode shows a quiet chip instead of an account and no logout", async () => {
    fakeFetch({ "/api/auth/session": { json: SESSION_DEV }, "/api/protocols": { json: { protocols: [] } }, "/api/workspace/experiments": { json: { experiments: [] } }, "/api/workspace/session": { json: { principal_id: "dev" } } });
    renderWithClient(<App />);
    expect(await screen.findByTestId("dev-chip")).toHaveTextContent("개발 모드");
    expect(screen.queryByTestId("logout-open")).toBeNull();
  });

  it("logging out asks first, then returns to the login", async () => {
    let signedIn = true;
    fakeFetch({
      "/api/auth/session": () => ({ json: signedIn ? SESSION_ACCOUNTS : SESSION_OUT }),
      "/api/auth/logout": () => {
        signedIn = false;
        return { json: { signed_out: true } };
      },
      "/api/protocols": { json: { protocols: [] } },
      "/api/workspace/experiments": { json: { experiments: [] } },
      "/api/workspace/session": { json: { principal_id: "a1" } },
    });
    renderWithClient(<App />);
    await userEvent.click(await screen.findByTestId("logout-open"));
    expect(screen.getByRole("dialog")).toHaveTextContent("로그아웃할까요?");
    await userEvent.click(screen.getByTestId("logout-confirm"));
    expect(await screen.findByRole("button", { name: "로그인하기" })).toBeInTheDocument();
  });

  it("a route the new screen does not have goes to the old screen unchanged", async () => {
    vi.stubGlobal("location", { ...window.location, hash: "#/records/s-9", search: "", pathname: "/app/", replace });
    fakeFetch({ "/api/auth/session": { json: SESSION_DEV } });
    renderWithClient(<App />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/index.html#/records/s-9"));
    expect(screen.getByRole("status")).toHaveTextContent("지금 화면으로 넘어가요…");
  });

  it("a session that expires on a list read returns to the login with a word", async () => {
    let signedIn = true;
    fakeFetch({
      "/api/auth/session": () => ({ json: signedIn ? SESSION_ACCOUNTS : SESSION_OUT }),
      "/api/protocols": () => {
        signedIn = false;
        return { status: 401, json: { detail: "authentication_required" } };
      },
      "/api/workspace/experiments": { json: { experiments: [] } },
      "/api/workspace/session": { json: { principal_id: "a1" } },
    });
    renderWithClient(<App />);
    expect(await screen.findByRole("button", { name: "로그인하기" })).toBeInTheDocument();
  });

  it("an app that is not ready (a 503 on the session route) says so, not 'login failed'", async () => {
    fakeFetch({ "/api/auth/session": { status: 503, json: { detail: "configuration_invalid" } } });
    renderWithClient(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("화면을 불러오지 못했어요. 잠시 뒤 다시 해 주세요.");
  });

  it("a server that cannot be reached says so and offers a retry", async () => {
    fakeFetch({ "/api/auth/session": new TypeError("Failed to fetch") });
    renderWithClient(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("연결이 안 돼요. 인터넷을 확인한 뒤 다시 해 주세요.");
    expect(screen.getByRole("button", { name: "다시 불러오기" })).toBeInTheDocument();
  });
});
