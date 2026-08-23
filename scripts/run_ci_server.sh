#!/usr/bin/env bash
# CI-safe server launcher for browser acceptance tests (Playwright).
#
# Unlike scripts/run_candidate_a.sh, this does not load the curated "Candidate
# A" fixture: that fixture's integrity model requires the exact, externally
# licensed source PDF (byte size, SHA-256, and page count all checked against
# recorded provenance) which is not and should not be committed to the repo.
# CI instead runs with an empty protocol catalog, which is a legitimate,
# already-supported server configuration - exactly the empty/loading state
# tests/e2e/empty-states.spec.ts exercises. No xAI-dependent optional feature
# is enabled, so CI never depends on live credentials.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTOCOL_DATA_DIR="$ROOT/data/runtime/ci-e2e"

# In GitHub Actions, actions/setup-python already puts a suitable `python` on
# PATH. Locally, activate the project venv if one exists and the caller
# hasn't already activated an environment.
if [[ -z "${VIRTUAL_ENV:-}" && -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

rm -rf "$PROTOCOL_DATA_DIR"

export VOICE_WORKFLOW_AGENT_PROTOCOL_ENABLED="true"
export VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR="$PROTOCOL_DATA_DIR"
export VOICE_WORKFLOW_AGENT_MOSS_ENABLED="false"
export VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED="true"
export VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB="$PROTOCOL_DATA_DIR/experiment_reports.sqlite"
export VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED="true"
export VOICE_WORKFLOW_AGENT_WORKSPACE_DATA_DIR="$PROTOCOL_DATA_DIR/workspace"
export EXTERNAL_REFERENCES_ENABLED="false"
export VOICE_WORKFLOW_AGENT_MULTI_BRAIN_ENABLED="false"
export SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED="false"
export WEB_VISUAL_SEARCH_ENABLED="false"
export VOICE_WORKFLOW_AGENT_GENERATED_VISUALS_ENABLED="false"

echo "=== Starting Voice Workflow Agent (CI, empty protocol catalog) ==="
exec python -B -m uvicorn \
  voice_workflow_agent.server:app \
  --host 127.0.0.1 \
  --port 8000
