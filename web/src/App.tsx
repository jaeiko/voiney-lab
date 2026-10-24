import { ApiError } from "./api/client";
import { useAuthSession } from "./api/queries";
import { AppFrame } from "./components/AppFrame";
import { ErrorBox, LoadingBox } from "./components/Pieces";
import { Logo } from "./components/Logo";
import { auth, frame, states } from "./copy/ko";
import { useHashRoute } from "./lib/useHashRoute";
import { HandoffScreen } from "./screens/HandoffScreen";
import { HomeScreen } from "./screens/HomeScreen";
import { LoginScreen } from "./screens/LoginScreen";
import { ProtocolsScreen } from "./screens/ProtocolsScreen";

export function inviteTokenOf(search: string): string | null {
  const token = new URLSearchParams(search).get("invite");
  return token && token.trim() ? token.trim() : null;
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="auth">
      <div className="auth-card">
        <div className="auth-brand">
          <Logo size={40} />
        </div>
        {children}
      </div>
    </div>
  );
}

export function App() {
  const session = useAuthSession();
  const route = useHashRoute();

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

  let screen;
  switch (route.name) {
    case "home":
      screen = <HomeScreen session={data} />;
      break;
    case "protocols":
      screen = <ProtocolsScreen />;
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
