// Server state through TanStack Query (lane RX0). The catalog is read again
// every 5 s while a protocol is being analysed and every 30 s otherwise, at
// once when the screen is looked at again, and not at all while it is hidden
// -- the same intervals as lane PRE's catalog refresh on the old screen.
// Lane RX1 added the review draft, the source pages and today's values.
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { apiFetch, ApiError } from "./client";
import type {
  AuthSession,
  CatalogEntry,
  Experiment,
  InviteInfo,
  ProtocolDetail,
  ReviewDraft,
  RunChangeOptions,
  SourcePage,
  SourcePages,
  WorkspaceSession,
} from "./types";
import { DEFAULT_TIME_ZONE, knownZone } from "../lib/clock";
import { entryAnalysing } from "../lib/protocolStatus";

export const CATALOG_REFRESH_RUNNING_MS = 5000;
export const CATALOG_REFRESH_IDLE_MS = 30000;

export const keys = {
  auth: ["auth", "session"] as const,
  invite: (token: string) => ["auth", "invite", token] as const,
  protocols: ["protocols"] as const,
  protocol: (id: string) => ["protocols", id] as const,
  reviewDraft: (id: string) => ["protocols", id, "review-draft"] as const,
  sourcePages: (id: string) => ["protocols", id, "source-pages"] as const,
  sourcePage: (id: string, page: number) => ["protocols", id, "source-pages", page] as const,
  runChanges: (id: string) => ["protocols", id, "run-changes"] as const,
  experiments: ["workspace", "experiments"] as const,
  workspaceSession: ["workspace", "session"] as const,
};

export function protocolPath(protocolId: string, rest = ""): string {
  return `/api/protocols/${encodeURIComponent(protocolId)}${rest}`;
}

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

export function useProtocolDetail(protocolId: string | null, enabled = true, refetchMs: number | false = CATALOG_REFRESH_RUNNING_MS) {
  return useQuery({
    queryKey: keys.protocol(protocolId ?? ""),
    queryFn: () => apiFetch<ProtocolDetail>(protocolPath(protocolId ?? "")),
    enabled: enabled && Boolean(protocolId),
    refetchInterval: refetchMs,
    refetchIntervalInBackground: false,
  });
}

/** The cards a person decides on, read once; a decision's answer replaces it through the client. */
export function useReviewDraft(protocolId: string | null) {
  return useQuery({
    queryKey: keys.reviewDraft(protocolId ?? ""),
    queryFn: () => apiFetch<ReviewDraft>(protocolPath(protocolId ?? "", "/review-draft")),
    enabled: Boolean(protocolId),
    refetchOnWindowFocus: false,
  });
}

export function useSourcePages(protocolId: string | null, enabled = true) {
  return useQuery({
    queryKey: keys.sourcePages(protocolId ?? ""),
    queryFn: () => apiFetch<SourcePages>(protocolPath(protocolId ?? "", "/review-draft/source-pages")),
    enabled: enabled && Boolean(protocolId),
    staleTime: Infinity,
  });
}

export function useSourcePage(protocolId: string | null, page: number | null) {
  return useQuery({
    queryKey: keys.sourcePage(protocolId ?? "", page ?? 0),
    queryFn: () => apiFetch<SourcePage>(protocolPath(protocolId ?? "", `/review-draft/source-pages/${page}`)),
    enabled: Boolean(protocolId) && Number.isInteger(page) && (page as number) > 0,
    staleTime: Infinity,
  });
}

export function sourcePageImageUrl(protocolId: string, page: number, size: "full" | "thumb" = "full"): string {
  return protocolPath(protocolId, `/review-draft/source-pages/${page}/image${size === "thumb" ? "?size=thumb" : ""}`);
}

export function useRunChanges(protocolId: string | null) {
  return useQuery({
    queryKey: keys.runChanges(protocolId ?? ""),
    queryFn: () => apiFetch<RunChangeOptions>(protocolPath(protocolId ?? "", "/start-check/changes")),
    enabled: Boolean(protocolId),
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export interface ExperimentList {
  experiments: Experiment[];
  /** The zone the screen says times in: the server's VOINEY_LAB_REPORT_TIMEZONE (lane UI1). */
  timezone: string;
}

async function fetchExperiments(): Promise<ExperimentList> {
  const payload = await apiFetch<{ experiments?: Experiment[]; timezone?: string }>("/api/workspace/experiments");
  return { experiments: Array.isArray(payload?.experiments) ? payload.experiments : [], timezone: knownZone(payload?.timezone) };
}

export function useExperiments(enabled = true) {
  return useQuery({
    queryKey: keys.experiments,
    queryFn: fetchExperiments,
    select: (list: ExperimentList) => list.experiments,
    enabled,
    refetchInterval: CATALOG_REFRESH_IDLE_MS,
    refetchIntervalInBackground: false,
    retry: false,
  });
}

/**
 * The lab's time zone (lane UI1): read with the records list, the default until it is in. One request serves both; a
 * screen that only needs the zone reads it once (no polling of its own).
 */
export function useTimeZone(): string {
  const list = useQuery({ queryKey: keys.experiments, queryFn: fetchExperiments, select: (data: ExperimentList) => data.timezone, staleTime: Infinity, retry: false });
  return list.data ?? DEFAULT_TIME_ZONE;
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

/** A protocol changed on the server (an upload, a decision, a new name): read the lists and the detail again. */
export function protocolChanged(client: QueryClient, protocolId?: string): void {
  void client.invalidateQueries({ queryKey: keys.protocols });
  if (protocolId) void client.invalidateQueries({ queryKey: keys.protocol(protocolId) });
}
