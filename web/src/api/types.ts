// The server's answers as the new screen reads them (lane RX0). The contract
// is the server's (server.py routes, protocol_catalog.ProtocolCatalogEntry
// .public_dict, workspace_store.list_experiments); nothing here is sent back
// as state.

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
}

export interface AnalysisRun {
  state: string | null;
  total_chunks?: number | null;
  completed_chunks?: number | null;
  failure_code?: string | null;
  requested_at?: string | null;
  reanalysis?: boolean;
  lifecycle_state?: string | null;
}

export interface ProtocolDetail extends CatalogEntry {
  analysis_run?: AnalysisRun | null;
}

export interface Experiment {
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
