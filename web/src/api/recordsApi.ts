import { apiFetch } from './client';
import type { Experiment } from './types';
import type { TimelineEvent } from './runApi';

export interface Recovery { checkpoints?: Array<{ step_id: string; step_label: string; carried_step_count: number }>; not_restored?: string[] }
export interface RecordEvent extends TimelineEvent {
  event_key?: string;
  user_wording?: string | null;
  payload?: { timer?: Record<string, unknown>; run_changes?: Array<Record<string, string>>; sources?: Array<Record<string, unknown>>; record_fix?: { target_event_key?: string; text_after?: string }; comment?: string };
}
export interface ReportSummary {
  report_id: string; session_id?: string; protocol_title?: string; status: string; started_at?: string;
  owner_principal_id?: string | null;
}
export interface ReviewValue {
  item_id: string; step_label?: string; kind: string; text: string; confirmed: boolean;
  corrected?: boolean; original?: string;
}
export interface Notebook {
  title: string; subtitle: string; header_rows: string[][];
  purpose_words: string; samples: Array<{ name: string; condition: string; control: boolean }>;
  condition_rows: string[][]; condition_note: string; materials_words: string;
  observations: Array<{ label: string; at: string; kind: string; shown: string }>;
  result: string; result_words: string; next_change: string; differences: string[];
  previous_words: string; previous_lines: string[]; next_lines: string[]; pi_rows: string[][];
  appendix_step_rows: string[][]; appendix_record_rows: string[][]; source_words: string; system_words: string;
}
export interface NotebookResponse {
  report: ReportSummary; notebook: Notebook | null; review: ReviewValue[]; events: RecordEvent[];
}
const enc = encodeURIComponent;
export const experimentPath = (id: string, suffix = '') => `/api/workspace/experiments/${enc(id)}${suffix}`;
export const reportPath = (id: string, suffix: string) => `/api/experiment-reports/${enc(id)}${suffix}`;
export const recordsApi = {
  experiment: (id: string) => apiFetch<Experiment>(experimentPath(id)),
  timeline: (id: string) => apiFetch<{ timeline: RecordEvent[]; session?: { version: number }; recovery?: Recovery }>(experimentPath(id, '/timeline')),
  reports: (id: string) => apiFetch<{ reports: ReportSummary[] }>(experimentPath(id, '/reports')),
  notebook: (id: string) => apiFetch<NotebookResponse>(reportPath(id, '/notebook')),
};
