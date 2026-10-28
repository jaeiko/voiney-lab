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
export interface NotebookMethodRow { label: string; source_text: string; actual_values: string; started_at: string; ended_at: string; timer: string; source_page?: number | null }
export interface NotebookTimelineItem { at: string; step_label: string; kind: string; text: string }
export interface NotebookValueBar { label: string; kind: string; source: string; actual: string; source_number: string; actual_number: string; unit: string }
export interface NotebookImage { kind: 'photo' | 'source_figure'; step_label: string; caption: string; url: string; media_type: string; asset_id: string; source_page?: number | null; interpretation_status?: string }
export interface NotebookDomainField { label: string; value: string; origin: 'source' | 'record' | 'calculated' | 'blank'; source_keys?: string[]; formula?: string }
export interface NotebookInventoryItem { kind: string; name: string; vendor: string; catalog_number: string; lot_number: string; stage: string; source_keys?: string[] }
export interface NotebookSection { id: string; name: string }
export interface Notebook {
  title: string; subtitle: string; header_rows: string[][];
  purpose_words: string; samples: Array<{ name: string; condition: string; control: boolean }>;
  condition_rows: string[][]; condition_note: string; materials_words: string;
  observations: Array<{ label: string; at: string; kind: string; shown: string }>;
  result: string; result_words: string; next_change: string; differences: string[];
  previous_words: string; previous_lines: string[]; next_lines: string[]; pi_rows: string[][];
  appendix_step_rows: string[][]; appendix_record_rows: string[][]; source_words: string; system_words: string;
  method_rows?: NotebookMethodRow[]; timeline?: NotebookTimelineItem[]; value_bars?: NotebookValueBar[];
  images?: NotebookImage[]; plate_positions?: Array<{ label: string; step_label: string; words: string }>;
  sample_rows?: Array<{ identifier: string; records: string[] }>;
  domain?: { code: string; label: string; selected_by: string; fields: NotebookDomainField[] };
  inventory?: NotebookInventoryItem[]; interpretation_words?: string; section_template?: NotebookSection[];
}
export interface NotebookResponse {
  report: ReportSummary; notebook: Notebook | null; review: ReviewValue[]; events: RecordEvent[];
}
export interface SharingResponse { report_id: string; shared: boolean; changed_at: string | null; event_inserted?: boolean }
export interface ComparisonRecord {
  record_scope: 'mine' | 'shared'; label: string; report_id?: string; started_at?: string; ended_at?: string | null;
  protocol_revision?: string; value_rows: Array<{ step_label: string; kind: string; source: string; actual: string; unit: string }>;
  method_rows: Array<{ step_label: string; source_text: string; actual_values: string; started_at: string; ended_at: string; timer: string }>;
  timeline: NotebookTimelineItem[]; notes: Array<{ step_label: string; at: string; kind: string; text: string }>;
}
export interface ComparisonResponse {
  anchor_report_id?: string; records: ComparisonRecord[];
  permission: { scope?: string; other_records?: string; withdrawal?: string; judgment: string };
}
export interface NotebookTemplate { version: number; sections: NotebookSection[]; created_at?: string | null }
const enc = encodeURIComponent;
export const experimentPath = (id: string, suffix = '') => `/api/workspace/experiments/${enc(id)}${suffix}`;
export const reportPath = (id: string, suffix: string) => `/api/experiment-reports/${enc(id)}${suffix}`;
export const recordsApi = {
  experiment: (id: string) => apiFetch<Experiment>(experimentPath(id)),
  timeline: (id: string) => apiFetch<{ timeline: RecordEvent[]; session?: { version: number }; recovery?: Recovery }>(experimentPath(id, '/timeline')),
  reports: (id: string) => apiFetch<{ reports: ReportSummary[] }>(experimentPath(id, '/reports')),
  notebook: (id: string) => apiFetch<NotebookResponse>(reportPath(id, '/notebook')),
  sharing: (id: string) => apiFetch<SharingResponse>(reportPath(id, '/sharing')),
  changeSharing: (id: string, shared: boolean) => apiFetch<SharingResponse>(reportPath(id, '/sharing'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ shared }),
  }),
  comparisons: (id: string) => apiFetch<ComparisonResponse>(reportPath(id, '/comparisons')),
  selectDomain: (id: string, domain: string) => apiFetch(reportPath(id, '/notebook/domain'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ domain }),
  }),
  recordInventory: (id: string, body: { item_name: string; vendor: string; catalog_number: string; lot_number: string; stage: string }) => apiFetch(reportPath(id, '/notebook/inventory'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  notebookTemplate: () => apiFetch<NotebookTemplate>('/api/notebook-template'),
  saveNotebookTemplate: (sections: NotebookSection[]) => apiFetch<NotebookTemplate>('/api/notebook-template', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sections }),
  }),
};
