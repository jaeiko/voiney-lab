// Server state through TanStack Query (lane RX0). The catalog is read again
// every 5 s while a protocol is being analysed and every 30 s otherwise, at
// once when the screen is looked at again, and not at all while it is hidden
// -- the same intervals as lane PRE's catalog refresh on the old screen.
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { apiFetch, ApiError } from "./client";
import type { AuthSession, CatalogEntry, Experiment, InviteInfo, ProtocolDetail, WorkspaceSession } from "./types";
import { entryAnalysing } from "../lib/protocolStatus";

export const CATALOG_REFRESH_RUNNING_MS = 5000;
export const CATALOG_REFRESH_IDLE_MS = 30000;

export const keys = {
  auth: ["auth", "session"] as const,
  invite: (token: string) => ["auth", "invite", token] as const,
  protocols: ["protocols"] as const,
  protocol: (id: string) => ["protocols", id] as const,
  experiments: ["workspace", "experiments"] as const,
  workspaceSession: ["workspace", "session"] as const,
};

export function useAuthSession() {
  return useQuery({
    queryKey: keys.auth,
    queryFn: () => apiFetch<AuthSession>("/api/auth/session"),
    staleTime: Infinity,
    retry: false,
  });
}

export function useInvite(token: string | null) {
  return useQuery({
    queryKey: keys.invite(token ?? ""),
    queryFn: () => apiFetch<InviteInfo>(`/api/auth/invites/${encodeURIComponent(token ?? "")}`),
    enabled: Boolean(token),
    staleTime: Infinity,
    retry: false,
  });
}

export function catalogRefreshInterval(entries: CatalogEntry[] | undefined): number {
  return entries?.some((entry) => entryAnalysing(entry)) ? CATALOG_REFRESH_RUNNING_MS : CATALOG_REFRESH_IDLE_MS;
}

export function useProtocols(enabled = true) {
  return useQuery({
    queryKey: keys.protocols,
    queryFn: async () => {
      const payload = await apiFetch<{ protocols?: CatalogEntry[] }>("/api/protocols");
      return Array.isArray(payload?.protocols) ? payload.protocols : [];
    },
    enabled,
    refetchInterval: (query) => catalogRefreshInterval(query.state.data),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
}

export function useProtocolDetail(protocolId: string | null, enabled = true) {
  return useQuery({
    queryKey: keys.protocol(protocolId ?? ""),
    queryFn: () => apiFetch<ProtocolDetail>(`/api/protocols/${encodeURIComponent(protocolId ?? "")}`),
    enabled: enabled && Boolean(protocolId),
    refetchInterval: CATALOG_REFRESH_RUNNING_MS,
    refetchIntervalInBackground: false,
  });
}

export function useExperiments(enabled = true) {
  return useQuery({
    queryKey: keys.experiments,
    queryFn: async () => {
      const payload = await apiFetch<{ experiments?: Experiment[] }>("/api/workspace/experiments");
      return Array.isArray(payload?.experiments) ? payload.experiments : [];
    },
    enabled,
    refetchInterval: CATALOG_REFRESH_IDLE_MS,
    refetchIntervalInBackground: false,
    retry: false,
  });
}

export function useWorkspaceSession(enabled = true) {
  return useQuery({
    queryKey: keys.workspaceSession,
    queryFn: () => apiFetch<WorkspaceSession>("/api/workspace/session"),
    enabled,
    staleTime: Infinity,
    retry: false,
  });
}

/** The session is gone (a 401 from a signed-in route, or a logout): forget the lists and ask who is signed in again. */
export function signedOut(client: QueryClient): void {
  client.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth" });
  void client.invalidateQueries({ queryKey: keys.auth });
}

export function isSessionExpired(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

/** After the render: a 401 on a signed-in read sends the screen back to the login. */
export function useSignOutOnExpiry(error: unknown): void {
  const client = useQueryClient();
  const expired = isSessionExpired(error);
  useEffect(() => {
    if (expired) signedOut(client);
  }, [expired, client]);
}
