import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fakeFetch, renderWithClient, SESSION_ACCOUNTS } from "../test/helpers";
import { LoginScreen } from "./LoginScreen";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("LoginScreen", () => {
  it("shows the name, the tagline and one button; no step number", () => {
    fakeFetch({});
    renderWithClient(<LoginScreen invite={null} />);
    expect(screen.getByRole("img", { name: "Voiney Lab" })).toHaveAttribute("src", expect.stringContaining("brand/voiney-lab_logo_ink.svg"));
    expect(screen.getByText("장갑을 낀 채 말로 진행하는 실험 안내")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "로그인하기" })).toBeInTheDocument();
    expect(screen.queryByText(/1 \/ 6/)).not.toBeInTheDocument();
    expect(screen.getByText("처음이면 받은 초대 링크를 열어 주세요.")).toBeInTheDocument();
  });

  it("asks for both fields before sending anything (empty state)", async () => {
    const { calls } = fakeFetch({});
    renderWithClient(<LoginScreen invite={null} />);
    await userEvent.click(screen.getByRole("button", { name: "로그인하기" }));
    expect(screen.getByTestId("auth-error")).toHaveTextContent("이름과 비밀번호를 적어 주세요.");
    expect(calls).toEqual([]);
  });

  it("a wrong password says so (error state)", async () => {
    fakeFetch({ "/api/auth/login": { status: 401, json: { detail: "login_refused" } } });
    renderWithClient(<LoginScreen invite={null} />);
    await userEvent.type(screen.getByLabelText("이름"), "재준");
    await userEvent.type(screen.getByLabelText("비밀번호"), "wrong-pass");
    await userEvent.click(screen.getByRole("button", { name: "로그인하기" }));
    await waitFor(() => expect(screen.getByTestId("auth-error")).toHaveTextContent("이름이나 비밀번호가 맞지 않아요."));
  });

  it("a lock (429) says how many minutes from the server's Retry-After", async () => {
    fakeFetch({ "/api/auth/login": { status: 429, json: { detail: "login_locked" }, headers: { "Retry-After": "540" } } });
    renderWithClient(<LoginScreen invite={null} />);
    await userEvent.type(screen.getByLabelText("이름"), "재준");
    await userEvent.type(screen.getByLabelText("비밀번호"), "pass-word");
    await userEvent.click(screen.getByRole("button", { name: "로그인하기" }));
    await waitFor(() => expect(screen.getByTestId("auth-error")).toHaveTextContent("여러 번 틀려 잠시 막혔어요. 9분 뒤에 다시 해 주세요."));
  });

  it("a lost connection says so", async () => {
    fakeFetch({ "/api/auth/login": new TypeError("Failed to fetch") });
    renderWithClient(<LoginScreen invite={null} />);
    await userEvent.type(screen.getByLabelText("이름"), "재준");
    await userEvent.type(screen.getByLabelText("비밀번호"), "pass-word");
    await userEvent.click(screen.getByRole("button", { name: "로그인하기" }));
    await waitFor(() => expect(screen.getByTestId("auth-error")).toHaveTextContent("연결이 안 돼요. 인터넷을 확인한 뒤 다시 해 주세요."));
  });

  it("a successful login sends name and password and refreshes the session (normal state)", async () => {
    const { calls } = fakeFetch({
      "/api/auth/login": { json: { account: SESSION_ACCOUNTS.account } },
      "/api/auth/session": { json: SESSION_ACCOUNTS },
    });
    const onSignedIn = vi.fn();
    renderWithClient(<LoginScreen invite={null} onSignedIn={onSignedIn} />);
    await userEvent.type(screen.getByLabelText("이름"), "재준");
    await userEvent.type(screen.getByLabelText("비밀번호"), "pass-word");
    await userEvent.click(screen.getByRole("button", { name: "로그인하기" }));
    await waitFor(() => expect(onSignedIn).toHaveBeenCalled());
    const login = calls.find((call) => call.path === "/api/auth/login");
    expect(login?.init?.method).toBe("POST");
    expect(JSON.parse(String(login?.init?.body))).toEqual({ name: "재준", password: "pass-word" });
    expect(login?.init?.redirect).toBe("error");
  });

  it("an invite link opens the signup with the lab's name (loading, then normal)", async () => {
    fakeFetch({ "/api/auth/invites/tok-1": { json: { valid: true, organization_id: "lab-a", admin: true } } });
    renderWithClient(<LoginScreen invite="tok-1" />);
    expect(screen.getByTestId("auth-lead")).toHaveTextContent("초대 링크를 확인하는 중…");
    await waitFor(() => expect(screen.getByTestId("auth-lead")).toHaveTextContent("연구실 lab-a의 초대예요. 관리자로 가입해요."));
    expect(screen.getByRole("button", { name: "가입하고 시작하기" })).toBeEnabled();
  });

  it("a spent invite says so and cannot be sent", async () => {
    fakeFetch({ "/api/auth/invites/tok-old": { status: 404, json: { detail: "invite_invalid" } } });
    renderWithClient(<LoginScreen invite="tok-old" />);
    await waitFor(() => expect(screen.getByTestId("auth-lead")).toHaveTextContent("초대 링크가 만료되었거나 이미 쓰였어요"));
    expect(screen.getByRole("button", { name: "가입하고 시작하기" })).toBeDisabled();
  });
});
