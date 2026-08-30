#!/usr/bin/env bash
# Credential-free server launcher for browser acceptance tests (Playwright).
#
# No xAI-dependent optional feature is enabled here, so this launcher never
# depends on live credentials and never leaves a browser run waiting on a
# long-lived provider request.
#
# The curated "Candidate A" fixture is loaded ONLY when its externally licensed
# source PDF is actually present. That fixture's integrity model checks byte
# size, SHA-256, and page count against recorded provenance, and the PDF is not
# and should not be committed to the repo. In CI it is absent by design, so the
# server creates a source-grounded, explicitly FICTIONAL NON-OPERATIONAL browser
# fixture in the throwaway directory. This lets the real reviewer and checkpoint
# paths run without proprietary material, live providers, or safety claims.
#
# Either way this writes to its own throwaway data directory, so a developer's
# own pilot state is never touched by a browser run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTOCOL_DATA_DIR="$ROOT/data/runtime/ci-e2e"
# Allow a non-default port so a browser run never collides with a developer's
# own long-running server on 8000.
APP_PORT="${PLAYWRIGHT_APP_PORT:-8000}"

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
# Keep browser acceptance deterministic even when a developer shell or local
# dotenv file contains a live provider credential. CI exercises the complete
# offline failure/fallback contract and must never call a provider implicitly.
export XAI_API_KEY=""
export VOICE_WORKFLOW_AGENT_PRESENTATION_TRANSLATION_ENABLED="0"

FIXTURE="$ROOT/data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE="$ROOT/data/development_protocols/candidate_a_curated_analysis.provenance.json"
SOURCE_PDF="${CANDIDATE_A_SOURCE_PDF:-$ROOT/data/runtime/candidate-a-source/in-gel-digestion.pdf}"
CATALOG_STATE="empty protocol catalog"

if [[ -f "$FIXTURE" && -f "$PROVENANCE" && -f "$SOURCE_PDF" ]]; then
  export VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_FIXTURE="$FIXTURE"
  export VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_PROVENANCE="$PROVENANCE"
  export VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_SOURCE_PDF="$SOURCE_PDF"
  echo "=== Loading curated fixture into the throwaway browser-test catalog ==="
  python -B "$ROOT/scripts/bootstrap_browser_test_catalog.py"
  CATALOG_STATE="curated development fixture"
else
  echo "=== Candidate A source PDF absent; loading fictional non-operational browser fixture ==="
  python -B "$ROOT/scripts/bootstrap_browser_test_catalog.py"
  CATALOG_STATE="fictional non-operational browser fixture"
fi

echo "=== Starting Voice Workflow Agent ($CATALOG_STATE) on port $APP_PORT ==="
exec python -B -m uvicorn \
  voice_workflow_agent.server:app \
  --host 127.0.0.1 \
  --port "$APP_PORT"
