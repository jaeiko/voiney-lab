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
# banner prints is exported below, which is what makes the banner accurate:
# the optional features export the shell's or the .env's own value when a
# person set one (lane XO), and the safety settings are fixed here.
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

# Refuse to start while an old setting name is set in the environment or
# the repository .env (decision of 2026-10-04; scripts/migrate_env.py).
python -B -m voiney_lab.setting_names || exit 1

# --- Durable pilot state -----------------------------------------------------
export VOINEY_LAB_PROTOCOL_ENABLED="true"
export VOINEY_LAB_PROTOCOL_DATA_DIR="$PILOT_DATA_DIR"
export VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED="true"
export VOINEY_LAB_EXPERIMENT_REPORT_DB="$PILOT_DATA_DIR/experiment_reports.sqlite"
export VOINEY_LAB_WORKSPACE_ENABLED="true"
export VOINEY_LAB_WORKSPACE_DATA_DIR="$PILOT_DATA_DIR/workspace"

# --- Off unless a person asks for it -----------------------------------------
# These four reach outside the approved source documents. Each keeps a value
# the operator set in the shell or wrote in the repository .env (decision 1 of
# lane XO, 2026-10-05), so turning one on is a deliberate act. Three of them,
# and semantic intent, are features only xAI provides: one switched on without
# XAI_API_KEY is refused below, by name.
eval "$(python -B -m voiney_lab.configuration --launcher-defaults "$ROOT/.env" \
  VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED=false \
  VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED=false \
  VOINEY_LAB_WEB_VISUAL_SEARCH_ENABLED=false \
  VOINEY_LAB_GENERATED_VISUALS_ENABLED=false \
  VOINEY_LAB_SEMANTIC_INTENT_ENABLED=false)"

# --- Outside the pilot's scope -----------------------------------------------
# MOSS (the org-governed approved-safety-document corpus) has its own flag.
export VOINEY_LAB_MOSS_ENABLED="${VOINEY_LAB_MOSS_ENABLED:-false}"
# Dry-lab workflows and the eLabFTW ELN write-back have no launcher flag of
# their own: both sit behind the commercial workspace, which the reviewer
# inbox, protocol library and experiment timeline also need, so disabling it
# would take the pilot's own approval path with it. Both paths are inert
# until an admin creates and verifies a connector for them.

# --- Test mode is never on in a pilot ----------------------------------------
if [[ "${VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES:-}" =~ ^([1]|[Tt]rue|[Yy]es|[Oo]n)$ ]]; then
  echo "[WARN] VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES was set in the"
  echo "[WARN] environment. A pilot never skips readiness gates; forcing it off."
fi
export VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES="false"

# --- Approved safety documents: decided here, never by a .env ---------------
# operational needs OIDC, which the pilot does not have yet: without it the
# identity resolver refuses every /api and /ws request. demo and test_only
# need at least one document of that scope in the catalog, and every such
# document is demo material, which the check below refuses. reference_only is
# the scope left: development identity, approved non-demo documents, and a
# model told the material is non-operational.
PILOT_USAGE_SCOPE="reference_only"
PILOT_SAFETY_CATALOG="$PILOT_DATA_DIR/approved_safety_catalog.sqlite"
for fixed in \
  "VOINEY_LAB_USAGE_SCOPE=$PILOT_USAGE_SCOPE" \
  "VOINEY_LAB_SAFETY_CATALOG=$PILOT_SAFETY_CATALOG"; do
  name="${fixed%%=*}"
  if [[ -n "${!name:-}" && "${!name}" != "${fixed#*=}" ]]; then
    echo "[WARN] $name was set in the environment. The pilot ignores it and"
    echo "[WARN] uses ${fixed#*=}."
  fi
done
export VOINEY_LAB_USAGE_SCOPE="$PILOT_USAGE_SCOPE"
export VOINEY_LAB_SAFETY_CATALOG="$PILOT_SAFETY_CATALOG"

# Counted read-only by the rule the safety pack uses, so a demo document can
# never reach the safety panel of a pilot run.
SAFETY_DEMO_DOCUMENTS="-"
SAFETY_SCOPE_DOCUMENTS="-"
SAFETY_REFUSAL=""
if [[ ! -f "$VOINEY_LAB_SAFETY_CATALOG" ]]; then
  SAFETY_REFUSAL="no approved safety catalog at $VOINEY_LAB_SAFETY_CATALOG"
elif ! safety_counts="$(python -B - "$VOINEY_LAB_SAFETY_CATALOG" \
    "$VOINEY_LAB_USAGE_SCOPE" <<'PY'
import sqlite3
import sys

from voiney_lab.safety_pack import count_catalog_documents

try:
    print(*count_catalog_documents(sys.argv[1], sys.argv[2]))
except sqlite3.Error as exc:
    print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY
)"; then
  SAFETY_REFUSAL="the safety catalog cannot be read as an approved catalog"
else
  read -r SAFETY_DEMO_DOCUMENTS SAFETY_SCOPE_DOCUMENTS <<<"$safety_counts"
  if [[ "$SAFETY_DEMO_DOCUMENTS" != "0" ]]; then
    SAFETY_REFUSAL="the safety catalog holds $SAFETY_DEMO_DOCUMENTS demo document(s) (scope demo/test_only or a fictional title)"
  elif [[ "$SAFETY_SCOPE_DOCUMENTS" == "0" ]]; then
    SAFETY_REFUSAL="the safety catalog has no approved active $VOINEY_LAB_USAGE_SCOPE document; the server would reject every request"
  fi
fi

feature_state() {
  case "${1:-}" in
    1|true|True|TRUE|yes|Yes|on|On) echo "enabled" ;;
    *) echo "disabled" ;;
  esac
}

echo "=== Voiney Lab — controlled pilot ==="
echo
echo "--- Listen address ---"
echo "HOST = $HOST   (override: HOST=...)"
echo "PORT = $PORT   (override: PORT=...)"
echo
echo "--- Data paths ---"
echo "DATA_ROOT  = $PILOT_DATA_DIR"
echo "CATALOG    = $VOINEY_LAB_PROTOCOL_DATA_DIR/protocol_workspace.sqlite"
echo "ASSET_ROOT = $VOINEY_LAB_PROTOCOL_DATA_DIR/objects/sha256"
echo "REPORT_DB  = $VOINEY_LAB_EXPERIMENT_REPORT_DB"
echo "WORKSPACE  = $VOINEY_LAB_WORKSPACE_DATA_DIR"
echo
echo "--- Approved safety documents (fixed by this launcher) ---"
echo "SAFETY_CATALOG = $VOINEY_LAB_SAFETY_CATALOG"
echo "USAGE_SCOPE    = $VOINEY_LAB_USAGE_SCOPE"
echo "demo documents = $SAFETY_DEMO_DOCUMENTS"
echo "approved active $VOINEY_LAB_USAGE_SCOPE documents = $SAFETY_SCOPE_DOCUMENTS"
echo
echo "--- Features ---"
report_feature() { printf '%-30s %s\n' "$1" "$(feature_state "${2:-}")"; }
report_feature "protocol_catalog:" "$VOINEY_LAB_PROTOCOL_ENABLED"
report_feature "experiment_reports:" "$VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED"
report_feature "workspace:" "$VOINEY_LAB_WORKSPACE_ENABLED"
report_feature "external_references:" "$VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED"
report_feature "supplemental_model_knowledge:" "$VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED"
report_feature "web_visual_search:" "$VOINEY_LAB_WEB_VISUAL_SEARCH_ENABLED"
report_feature "generated_visuals:" "$VOINEY_LAB_GENERATED_VISUALS_ENABLED"
report_feature "semantic_intent:" "$VOINEY_LAB_SEMANTIC_INTENT_ENABLED"
report_feature "moss_safety_documents:" "$VOINEY_LAB_MOSS_ENABLED"
report_feature "readiness_gate_test_mode:" "$VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES"
echo
echo "--- No launcher flag of their own (reported, not disabled) ---"
printf '%-30s %s\n' "dry_lab_workflows:" \
  "behind the workspace; inert without a verified connector"
printf '%-30s %s\n' "eln_writeback (eLabFTW):" \
  "behind the workspace; inert without a verified connector"

XAI_REFUSAL=""
if ! python -B -m voiney_lab.configuration --refuse-xai-only-without-key "$ROOT/.env"; then
  XAI_REFUSAL="an xAI-only feature is on without XAI_API_KEY (see the line above)"
fi

if [[ -n "$XAI_REFUSAL" ]]; then
  echo
  echo "[ERROR] $XAI_REFUSAL"
  echo "[ERROR] Refusing to start the pilot."
  exit 1
fi

if [[ -n "$SAFETY_REFUSAL" ]]; then
  echo
  echo "[ERROR] $SAFETY_REFUSAL"
  echo "[ERROR] Refusing to start the pilot. Put a reviewed catalog with no demo"
  echo "[ERROR] document at the SAFETY_CATALOG path above (see"
  echo "[ERROR] docs/APPROVED_DOCUMENT_OPERATIONS.md) and run this again."
  exit 1
fi

if [[ "$CHECK_ONLY" == "true" ]]; then
  echo
  echo "[OK] --check-only: configuration printed; server not started"
  exit 0
fi

mkdir -p "$PILOT_DATA_DIR"

echo
echo "=== Starting Voiney Lab ==="

exec python -B -m uvicorn \
  voiney_lab.server:app \
  --host "$HOST" \
  --port "$PORT"
