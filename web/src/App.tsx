import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { ApiError } from "./api/client";
import { keys, useAuthSession } from "./api/queries";
import { AppFrame } from "./components/AppFrame";
import { ErrorBox, LoadingBox } from "./components/Pieces";
import { Logo } from "./components/Logo";
import { auth, frame, states } from "./copy/ko";
import { useHashRoute } from "./lib/useHashRoute";
import { RecordsScreen, RecordDetailScreen, NotebookScreen } from "./screens/RecordsScreen";
import { AnalysisScreen } from "./screens/AnalysisScreen";
import { HandoffScreen } from "./screens/HandoffScreen";
import { HomeScreen } from "./screens/HomeScreen";
import { LoginScreen } from "./screens/LoginScreen";
import { ProtocolDetailScreen } from "./screens/ProtocolDetailScreen";
import { ProtocolsScreen } from "./screens/ProtocolsScreen";
import { RunScreen } from "./screens/RunScreen";
import { SettingsScreen } from "./screens/SettingsScreen";
import { StartScreen } from "./screens/StartScreen";
import { welcomeDue, WelcomeScreen } from "./screens/WelcomeScreen";
import { go, routes } from "./lib/navigation";
import { useRun } from "./voice/runStore";
import { ReviewScreen } from "./screens/ReviewScreen";
import { RunChangesScreen } from "./screens/RunChangesScreen";
import { UploadScreen } from "./screens/UploadScreen";

export function inviteTokenOf(search: string): string | null {
  const token = new URLSearchParams(search).get("invite");
  return token && token.trim() ? token.trim() : null;
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="auth">
      <div className="auth-card">
        <div className="auth-brand">
          <Logo height={48} />
        </div>
        {children}
      </div>
    </div>
  );
}

/** The voice code asks who is signed in when the server closed its socket for the account (1008): the session is read again. */
function useAccountCheck(): void {
  const client = useQueryClient();
  useEffect(() => {
    const check = () => void client.invalidateQueries({ queryKey: keys.auth });
    window.addEventListener("voiney-lab:account-check", check);
    return () => window.removeEventListener("voiney-lab:account-check", check);
  }, [client]);
}

export function App() {
  const session = useAuthSession();
  const route = useHashRoute();
  const run = useRun();
  useAccountCheck();
  const signedIn = session.data && !(session.data.mode === "accounts" && !session.data.signed_in) ? session.data : null;
  const guide = signedIn !== null && route.name === "home" && welcomeDue(signedIn);
  useEffect(() => {
    // Lane RX2, decision 6: the first guide once after a login, before home.
    if (guide) go(routes.welcome, true);
  }, [guide]);

  if (session.isPending) {
    return (
      <Centered>
        <LoadingBox label={frame.checking} />
      </Centered>
    );
  }
  if (session.isError) {
    // The session route failed: no network at all, or the app itself is not ready (a 503 by a setting's name).
    const message = session.error instanceof ApiError && session.error.code !== "network" ? states.appFailed : auth.errors.network;
    return (
      <Centered>
        <ErrorBox message={message} onRetry={() => void session.refetch()} />
      </Centered>
    );
  }
  const data = session.data;
  if (data.mode === "accounts" && !data.signed_in) {
    return <LoginScreen invite={inviteTokenOf(window.location.search)} />;
  }

  // The run has the whole screen while it runs; with no run, the experiment tab is an ordinary page (no run bar).
  const running = run.active || run.phase === "starting" || run.phase === "stopping" || run.voice === "RECONNECTING";
  if (route.name === "run" && running) return <RunScreen session={data} />;
  if (route.name === "welcome") return <WelcomeScreen session={data} />;

  let screen;
  switch (route.name) {
    case "home":
      screen = <HomeScreen session={data} />;
      break;
    case "protocol":
      screen = <ProtocolDetailScreen key={route.protocolId} protocolId={route.protocolId!} session={data} />;
      break;
    case "protocols":
      screen = <ProtocolsScreen />;
      break;
    case "start":
      screen = <StartScreen key={route.hash} protocolId={route.protocolId ?? null} resumeId={route.resumeId ?? null} step={route.step ?? null} />;
      break;
    case "run":
      screen = <RunScreen session={data} />;
      break;
    case "settings":
      screen = <SettingsScreen session={data} />;
      break;
    case "upload":
      screen = <UploadScreen session={data} />;
      break;
    case "analysis":
      screen = <AnalysisScreen key={route.protocolId} protocolId={route.protocolId ?? ""} />;
      break;
    case "review":
      screen = <ReviewScreen key={route.protocolId} protocolId={route.protocolId ?? ""} session={data} />;
      break;
    case "changes":
      screen = <RunChangesScreen key={route.protocolId} protocolId={route.protocolId ?? ""} />;
      break;
    case "records":
      screen = <RecordsScreen session={data} />;
      break;
    case "record":
      screen = <RecordDetailScreen key={route.sessionId} sessionId={route.sessionId!} />;
      break;
    case "notebook":
      screen = <NotebookScreen key={route.reportId} sessionId={route.sessionId!} reportId={route.reportId!} session={data} />;
      break;
    case "handoff":
      screen = <HandoffScreen hash={route.hash} />;
      break;
  }
  return (
    <AppFrame session={data} current={route.name}>
      {screen}
    </AppFrame>
  );
}
