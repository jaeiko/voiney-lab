// The server's answers as the new screen reads them (lane RX0). The contract
// is the server's (server.py routes, protocol_catalog.ProtocolCatalogEntry
// .public_dict, workspace_store.list_experiments); nothing here is sent back
// as state. Lane RX1 added the upload, analysis run, review draft, source
// page and today's-values shapes (protocol_catalog.review_draft /
// review_cards / review_source_page, start_check.RunChanges.options).

export interface Account {
  account_id: string;
  name: string;
  organization_id: string;
  admin: boolean;
  entitlement?: string | null;
  disabled?: boolean;
}

export interface AuthSession {
  mode: "dev" | "accounts";
  signed_in: boolean;
  account: Account | null;
}

export interface InviteInfo {
  valid: boolean;
  organization_id?: string;
  admin?: boolean;
  entitlement?: string | null;
  expires_at?: string;
}

export interface ReviewPending {
  needs_review_count: number;
  message_ko?: string;
}

export interface ReviewVersion {
  version_ko?: string;
  [key: string]: unknown;
}

export interface SourceDisclosure {
  recorded: boolean;
  status: "yes" | "no" | "unknown" | null;
  status_ko: string;
  note: string;
  recorded_at: string | null;
}

export interface CatalogEntry {
  protocol_id: string;
  title: string;
  source_filename: string | null;
  source_sha256?: string;
  revision_id?: string | null;
  readiness_status?: string;
  analysis_status?: string;
  step_count?: number | null;
  created_at?: string | null;
  available_for_execution: boolean;
  execution_blocker_codes?: string[];
  development_only?: boolean;
  lifecycle_state?: string;
  analysis_outdated_codes?: string[];
  analysis_outdated_ko?: string | null;
  review_pending?: ReviewPending | null;
  review_versions?: ReviewVersion[];
  current_review_version?: ReviewVersion | null;
  execution_blocked_reason?: string | null;
  source_disclosure?: SourceDisclosure;
}

export interface AnalysisChunk {
  chunk_id?: string;
  ordinal?: number | null;
  source_page_start?: number | null;
  source_page_end?: number | null;
  status?: string | null;
  failure_code?: string | null;
}

export interface AnalysisRun {
  state: string | null;
  total_chunks?: number | null;
  completed_chunks?: number | null;
  failed_chunks?: number | null;
  chunks?: AnalysisChunk[];
  failure_code?: string | null;
  requested_at?: string | null;
  reanalysis?: boolean;
  lifecycle_state?: string | null;
  available_for_execution?: boolean;
}

export interface ProtocolDetail extends CatalogEntry {
  analysis_run?: AnalysisRun | null;
}

export interface Experiment {
  version?: number;
  completed_step_count?: number;
  rating?: number | null;
  rating_state?: string;
  session_id: string;
  protocol_id?: string | null;
  protocol_title?: string | null;
  status: string;
  current_step_label?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
  owner_principal_id?: string | null;
}

export interface WorkspaceSession {
  principal_id: string;
  display_name?: string;
  organization_id?: string;
}

// --- Upload (POST /api/protocols, POST /api/protocols/import-link) ---

export interface UploadOcr {
  state?: string;
  automatic?: boolean;
  blocked?: string;
  ocr_page_numbers?: number[];
}

export interface UploadResponse {
  protocol: CatalogEntry;
  deduplicated?: boolean;
  ocr?: UploadOcr | null;
  review_suggestion?: { message_ko?: string } | null;
}

export interface SourcePages {
  protocol_id: string;
  page_count: number;
  thumbnail_dpi: number;
  image_dpi: number;
}

// --- Review draft (GET …/review-draft) and its cards ---

export interface LineSpan {
  page: number;
  first_line: number;
  last_line: number;
}

export interface CardSource {
  page?: number | null;
  excerpt?: string | null;
  continued_page?: number | null;
  continued_excerpt?: string | null;
  verified?: boolean;
  lines?: LineSpan | null;
  suggested_lines?: (LineSpan & { unique?: boolean }) | null;
  value_lines?: number[];
  value_boxes?: number[];
}

export interface CardChoice {
  choice_id: string;
  label_ko: string;
  request: { item_id: string; action: string; [key: string]: unknown } | null;
  needs?: string | null;
  route?: string | null;
  primary?: boolean;
  engine?: string | null;
}

export interface CardDecision {
  action?: string | null;
  value?: { text?: string | null; seconds?: number | null; [key: string]: unknown } | null;
  source_lines?: LineSpan | null;
  reviewer_name?: string | null;
  recorded_at?: string | null;
}

export interface CardReading {
  text?: string | null;
  engine?: string | null;
  lines?: LineSpan | null;
  [key: string]: unknown;
}

export interface CardValue {
  item_id: string;
  shown?: string | null;
  reason_ko?: string | null;
  readings?: CardReading[];
  lines?: LineSpan | null;
  choices: CardChoice[];
  decided?: boolean;
  decision?: CardDecision | null;
  current?: string | null;
}

export interface CardStep {
  label?: string | null;
  label_shown?: string | null;
  label_printed?: boolean;
  text?: string | null;
  [key: string]: unknown;
}

export interface ReviewCard {
  card_id: string;
  item_id: string;
  position: number;
  total: number;
  progress_ko?: string;
  type: string;
  kind: string;
  kind_ko: string;
  step_label?: string | null;
  page?: number | null;
  question_ko: string;
  reasons_ko?: string[];
  shown?: string | null;
  source?: CardSource | null;
  choices: CardChoice[];
  values?: CardValue[];
  proposal?: string | null;
  change?: { from?: string | null; to?: string | null } | null;
  readings?: CardReading[] | null;
  steps?: CardStep[] | null;
  memo_allowed?: boolean;
  decided: boolean;
  decision?: CardDecision | null;
  current?: string | null;
  closed_ko?: string | null;
}

export interface ReviewTarget {
  kind?: string;
  version?: number;
  revision_id?: string;
  analysis_id?: string;
  [key: string]: unknown;
}

export interface ReviewDraft {
  protocol_id: string;
  title: string;
  source_filename?: string | null;
  analysis_status?: string;
  available_for_execution?: boolean;
  review_pending?: ReviewPending | null;
  target: ReviewTarget | null;
  cards: ReviewCard[];
  next_card: number | null;
  counts?: { items?: number; needs_review?: number; decided?: number; undecided?: number; changed?: number };
  registrable?: boolean;
  registration_blockers?: Array<{ item_id?: string; message_ko?: string; source_check?: boolean }>;
  message_ko?: string;
}

export interface SourceLine {
  number: number;
  text: string;
  has_value?: boolean;
  unclear?: boolean;
  /** [x0, y0, x1, y1] in PDF points (y down), or null when the engine kept no block for it (lane RX1). */
  box?: number[] | null;
}

export interface SourcePage {
  protocol_id: string;
  page: number;
  page_count: number;
  ocr?: boolean;
  lines: SourceLine[];
  image_dpi?: number;
}

// --- Today's values (GET/POST …/start-check/changes) ---

export interface ChangeableValue {
  item_id: string;
  step_id: string;
  step_label: string;
  kind: string;
  kind_ko: string;
  number: string;
  unit: string;
  written: string;
  shown_ko: string;
  units_ko: string;
  start?: number;
  end?: number;
}

export interface RunStep {
  step_id: string;
  step_label: string;
  text: string;
  text_ko: string | null;
  value_ids: string[];
}

export interface RunChange {
  item_id: string;
  step_id?: string;
  step_label: string;
  kind: string;
  kind_ko?: string;
  source_number: string;
  source_unit: string;
  source_written?: string;
  number: string;
  unit: string;
  shown_ko?: string;
  source_shown_ko?: string;
  words_ko?: string;
  question_ko?: string;
}

export interface RunChangeOptions {
  protocol_id: string;
  target: Record<string, unknown>;
  revision_id?: string;
  values: ChangeableValue[];
  limit: number;
  steps?: RunStep[];
  last_time?: { changes: RunChange[]; report_id?: string | null; when_ko?: string } | null;
  lab_revision?: { revision_id: string; version: number; label_ko: string; changes: RunChange[]; recorded_at?: string } | null;
}

export interface RunChangesChosen {
  change_set_id: string;
  changes: RunChange[];
  source: string;
  lab_revision?: { revision_id: string; version: number; label_ko: string; changes: RunChange[] } | null;
}
