#!/usr/bin/env bash
# Controlled-pilot launcher.
#
# Unlike scripts/run_dev.sh this loads no development fixture, keeps its own
# data root, and turns every out-of-source-document feature off. Nothing here
# weakens a readiness gate or a source check: it only decides which optional
# subsystems are configured on.
#
# server.py calls load_dotenv(..., override=False), so a value exported by this
# script wins over the same key in a repo-root .env. Every flag the startup
# banner prints is exported below, which is what makes the banner accurate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Loopback by default; export HOST=0.0.0.0 to publish deliberately.
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"

PILOT_DATA_DIR="$ROOT/data/runtime/pilot"

CHECK_ONLY=false
while [[ "$#" -ne 0 ]]; do
  case "$1" in
    --check-only)
      CHECK_ONLY=true
      shift
      ;;
    *)
      echo "usage: $0 [--check-only]"
      exit 2
      ;;
  esac
done

cd "$ROOT"

if [[ ! -f ".venv/bin/activate" ]]; then
  echo "[ERROR] venv not found: $ROOT/.venv"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# --- Durable pilot state -----------------------------------------------------
export VOICE_WORKFLOW_AGENT_PROTOCOL_ENABLED="true"
export VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR="$PILOT_DATA_DIR"
export VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED="true"
export VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB="$PILOT_DATA_DIR/experiment_reports.sqlite"
export VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED="true"
export VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR="$PILOT_DATA_DIR/workspace"

# --- Off unless a person asks for it before startup --------------------------
# These four reach outside the approved source documents. Each keeps a value
# the operator exported themselves, so turning one on is a deliberate act.
export EXTERNAL_REFERENCES_ENABLED="${EXTERNAL_REFERENCES_ENABLED:-false}"
export SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED="${SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED:-false}"
export WEB_VISUAL_SEARCH_ENABLED="${WEB_VISUAL_SEARCH_ENABLED:-false}"
export VOICE_WORKFLOW_AGENT_GENERATED_VISUALS_ENABLED="${VOICE_WORKFLOW_AGENT_GENERATED_VISUALS_ENABLED:-false}"

# --- Outside the pilot's scope -----------------------------------------------
# MOSS (the org-governed approved-safety-document corpus) has its own flag.
export VOICE_WORKFLOW_AGENT_MOSS_ENABLED="${VOICE_WORKFLOW_AGENT_MOSS_ENABLED:-false}"
# Dry-lab workflows and the eLabFTW ELN write-back have no launcher flag of
# their own: both sit behind the commercial workspace, which the reviewer
# inbox, protocol library and experiment timeline also need, so disabling it
# would take the pilot's own approval path with it. Both paths are inert
# until an admin creates and verifies a connector for them.

# --- Test mode is never on in a pilot ----------------------------------------
if [[ "${VOICE_WORKFLOW_AGENT_TEST_MODE_SKIP_READINESS_GATES:-}" =~ ^([1]|[Tt]rue|[Yy]es|[Oo]n)$ ]]; then
  echo "[WARN] VOICE_WORKFLOW_AGENT_TEST_MODE_SKIP_READINESS_GATES was set in the"
  echo "[WARN] environment. A pilot never skips readiness gates; forcing it off."
fi
export VOICE_WORKFLOW_AGENT_TEST_MODE_SKIP_READINESS_GATES="false"

feature_state() {
  case "${1:-}" in
    1|true|True|TRUE|yes|Yes|on|On) echo "enabled" ;;
    *) echo "disabled" ;;
  esac
}

echo "=== Voice Workflow Agent — controlled pilot ==="
echo
echo "--- Listen address ---"
echo "HOST = $HOST   (override: HOST=...)"
echo "PORT = $PORT   (override: PORT=...)"
echo
echo "--- Data paths ---"
echo "DATA_ROOT  = $PILOT_DATA_DIR"
echo "CATALOG    = $VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR/protocol_workspace.sqlite"
echo "ASSET_ROOT = $VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR/objects/sha256"
echo "REPORT_DB  = $VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB"
echo "WORKSPACE  = $VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR"
echo
echo "--- Features ---"
report_feature() { printf '%-30s %s\n' "$1" "$(feature_state "${2:-}")"; }
report_feature "protocol_catalog:" "$VOICE_WORKFLOW_AGENT_PROTOCOL_ENABLED"
report_feature "experiment_reports:" "$VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED"
report_feature "workspace:" "$VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED"
report_feature "external_references:" "$EXTERNAL_REFERENCES_ENABLED"
report_feature "supplemental_model_knowledge:" "$SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED"
report_feature "web_visual_search:" "$WEB_VISUAL_SEARCH_ENABLED"
report_feature "generated_visuals:" "$VOICE_WORKFLOW_AGENT_GENERATED_VISUALS_ENABLED"
report_feature "moss_safety_documents:" "$VOICE_WORKFLOW_AGENT_MOSS_ENABLED"
report_feature "readiness_gate_test_mode:" "$VOICE_WORKFLOW_AGENT_TEST_MODE_SKIP_READINESS_GATES"
echo
echo "--- No launcher flag of their own (reported, not disabled) ---"
printf '%-30s %s\n' "dry_lab_workflows:" \
  "behind the workspace; inert without a verified connector"
printf '%-30s %s\n' "eln_writeback (eLabFTW):" \
  "behind the workspace; inert without a verified connector"

if [[ "$CHECK_ONLY" == "true" ]]; then
  echo
  echo "[OK] --check-only: configuration printed; server not started"
  exit 0
fi

mkdir -p "$PILOT_DATA_DIR"

echo
echo "=== Starting Voice Workflow Agent ==="

exec python -B -m uvicorn \
  voiney_lab.server:app \
  --host "$HOST" \
  --port "$PORT"
