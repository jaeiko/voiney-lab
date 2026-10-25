// The server routes the start flow, the experiment screen and the settings
// read and write (lane RX2). The contracts are the server's, as the old
// screen uses them (index.html's wizard, records and settings); nothing here
// decides a state.
import { apiFetch, ApiError, postJson } from "./client";

export interface StartQuestion {
  question_id: string;
  question_ko?: string;
  progress_ko?: string;
  source?: { excerpt?: string; page?: number; lines?: { first_line?: number; last_line?: number } };
  choices?: Array<{ choice_id: string; label_ko: string }>;
}

export interface StartCheck {
  enabled: boolean;
  questions: StartQuestion[];
  skipped_reason?: string | null;
  target?: Record<string, unknown> | null;
}

export interface StartAnswer {
  proceed?: boolean;
  message_ko?: string;
  followups?: Array<{ choice_id: string; label_ko: string; request?: Record<string, unknown> }>;
}

export interface SafetyNotice {
  step_label?: string | null;
  source_page_number?: number | null;
  primary_text?: string | null;
  source_text?: string | null;
}

export interface ProtocolReview {
  protocol_id: string;
  revision_id?: string | null;
  available_for_execution?: boolean;
  safety_notices?: SafetyNotice[];
  execution_notices?: Array<{ message_ko?: string; message?: string }>;
  safety_scope?: { notice?: string } | null;
}

export interface ChangeValue {
  item_id: string;
  step_label: string;
  kind_ko: string;
  shown_ko: string;
  units_ko: string;
}

export interface ChangesList {
  values: ChangeValue[];
  target?: Record<string, unknown> | null;
  last_time?: { when_ko?: string; changes?: Array<{ item_id: string; step_label: string; kind_ko: string; shown_ko: string }> } | null;
}

export interface ChosenChange {
  step_label?: string;
  kind_ko?: string;
  words_ko?: string;
}

export interface ChangesChosen {
  change_set_id?: string | null;
  changes?: ChosenChange[];
}

export interface ExperimenterSettings {
  confirm_mode?: string;
  ambient_mode?: string;
  speaker_sensitivity?: string;
  agent_voice?: string;
  voice_calibration?: string;
  [name: string]: string | undefined;
}

export interface AgentVoices {
  provider?: string;
  default_voice_id?: string;
  voices: Array<{ voice_id: string; label_ko: string }>;
  selected?: string;
  preview_text_ko?: string;
}

export interface TimelineEvent {
  event_id?: string;
  event_type: string;
  step_label?: string | null;
  created_at?: string;
  observation?: { content: string; current_content?: string; category?: string; capture_source?: string; corrected?: boolean; retracted?: boolean } | null;
  evidence?: { evidence_id?: string; original_filename?: string; media_type?: string } | null;
}

const enc = encodeURIComponent;

export const runApi = {
  review: (protocolId: string) => apiFetch<ProtocolReview>(`/api/protocols/${enc(protocolId)}/review`),
  startCheck: (protocolId: string) => apiFetch<StartCheck>(`/api/protocols/${enc(protocolId)}/start-check`),
  answer: (protocolId: string, request: Record<string, unknown>) => postJson<StartAnswer>(`/api/protocols/${enc(protocolId)}/start-check/answers`, request),
  safetyRead: (protocolId: string, target: Record<string, unknown>) =>
    postJson<{ ready?: boolean }>(`/api/protocols/${enc(protocolId)}/start-check/safety`, { target }),
  changes: (protocolId: string) => apiFetch<ChangesList>(`/api/protocols/${enc(protocolId)}/start-check/changes`),
  chooseChanges: (protocolId: string, body: Record<string, unknown>) => postJson<ChangesChosen>(`/api/protocols/${enc(protocolId)}/start-check/changes`, body),
  settings: async () => (await apiFetch<{ settings?: ExperimenterSettings }>("/api/experimenter/settings")).settings ?? {},
  saveSetting: async (name: string, value: string) =>
    (await apiFetch<{ settings?: ExperimenterSettings }>("/api/experimenter/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [name]: value }),
    })).settings ?? {},
  voices: () => apiFetch<AgentVoices>("/api/voice/voices"),
  timeline: (sessionId: string) => apiFetch<{ timeline?: TimelineEvent[] }>(`/api/workspace/experiments/${enc(sessionId)}/timeline`),
  experiment: (sessionId: string) => apiFetch<{ session_id: string; version?: number; status?: string; protocol_id?: string; current_step_label?: string | null }>(`/api/workspace/experiments/${enc(sessionId)}`),
  addMemo: (sessionId: string, content: string) =>
    postJson<unknown>(`/api/workspace/experiments/${enc(sessionId)}/observations`, { idempotency_key: idempotency("manual-observation"), content, category: "note" }),
  addPhoto: (sessionId: string, file: File) => {
    const query = new URLSearchParams({ filename: file.name, idempotency_key: idempotency("evidence") });
    return apiFetch<unknown>(`/api/workspace/experiments/${enc(sessionId)}/evidence?${query}`, {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    });
  },
  changePassword: (current: string, next: string) => postJson<unknown>("/api/auth/password", { current, new: next }),
  deleteAccount: (password: string) => postJson<unknown>("/api/auth/delete-account", { password }),
};

/** index.html's newExperimentIdempotency: one key per press, so a repeated request is the same record. */
export function idempotency(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.() ?? String(Date.now());
  return `${prefix}-${random}`.replace(/[^A-Za-z0-9._:@-]/g, "-");
}

/** The voice preview: the server's audio for one voice, as a blob. */
export async function voicePreview(voiceId: string): Promise<Blob> {
  let response: Response;
  try {
    response = await fetch("/api/voice/preview", {
      method: "POST",
      credentials: "same-origin",
      redirect: "error",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice_id: voiceId }),
    });
  } catch {
    throw new ApiError(0, "network");
  }
  if (!response.ok) throw new ApiError(response.status, "preview_failed");
  return response.blob();
}

/** The server's own sentence on a refusal, when it sent one (detail.message_ko or message_ko). */
export function serverWords(error: unknown): string | null {
  const payload = error instanceof ApiError ? error.payload : null;
  if (payload && typeof payload === "object") {
    const record = payload as { message_ko?: unknown; detail?: { message_ko?: unknown } };
    if (typeof record.message_ko === "string" && record.message_ko.trim()) return record.message_ko.trim();
    if (typeof record.detail?.message_ko === "string" && record.detail.message_ko.trim()) return record.detail.message_ko.trim();
  }
  return null;
}
