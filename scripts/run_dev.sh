#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FIXTURE="$ROOT/data/fixtures/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE="$ROOT/data/fixtures/development_protocols/candidate_a_curated_analysis.provenance.json"
SOURCE_PDF="${VOINEY_LAB_CANDIDATE_A_SOURCE_PDF:-$ROOT/data/runtime/candidate-a-source/in-gel-digestion.pdf}"
PROTOCOL_DATA_DIR="$ROOT/data/runtime/candidate-a-live-acceptance"

EXPECTED_PDF_SHA256="63d81102fb644fca21e1c2296b566987756f2964ece06758fe52c73ba9c00bd9"

# Bind loopback by default so a development server is not published to the
# local network by accident. Export HOST=0.0.0.0 before starting to reach it
# from another device on purpose.
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

BOOTSTRAP_ONLY=false
TEST_MODE=false
while [[ "$#" -ne 0 ]]; do
  case "$1" in
    --bootstrap-only)
      BOOTSTRAP_ONLY=true
      shift
      ;;
    --test-mode)
      TEST_MODE=true
      shift
      ;;
    *)
      echo "usage: $0 [--bootstrap-only] [--test-mode]"
      exit 2
      ;;
  esac
done

cd "$ROOT"

if [[ ! -f ".venv/bin/activate" ]]; then
  echo "[ERROR] venv not found: $ROOT/.venv"
  exit 1
fi

source .venv/bin/activate

# Refuse to start while an old setting name is set in the environment or
# the repository .env (decision of 2026-10-04; scripts/migrate_env.py).
python -B -m voiney_lab.setting_names || exit 1

echo "=== Candidate A configuration check ==="

for file in \
  "$FIXTURE" \
  "$PROVENANCE" \
  "$SOURCE_PDF"
do
  if [[ ! -f "$file" ]]; then
    echo "[ERROR] required file not found:"
    echo "  $file"
    exit 1
  fi

  echo "[OK] $file"
done

ACTUAL_PDF_SHA256="$(sha256sum "$SOURCE_PDF" | awk '{print $1}')"

if [[ "$ACTUAL_PDF_SHA256" != "$EXPECTED_PDF_SHA256" ]]; then
  echo "[ERROR] Candidate A source PDF SHA-256 mismatch"
  echo "expected: $EXPECTED_PDF_SHA256"
  echo "actual:   $ACTUAL_PDF_SHA256"
  exit 1
fi

echo "[OK] Candidate A PDF SHA-256 verified"

export VOINEY_LAB_CURATED_PROTOCOL_FIXTURE="$FIXTURE"
export VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE="$PROVENANCE"
export VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF="$SOURCE_PDF"
# Registering a PDF in the browser POSTs /api/protocols/{id}/analysis straight
# away, which spends a provider call with no confirmation. This launcher walks
# the pre-analysed curated fixture and needs no analysis of its own, so the
# model name is cleared here: require_env treats an empty value as unset, the
# route answers provider_configuration_missing, and an accidental upload during
# a walkthrough cannot reach the budget. Registration and the source record are
# unaffected. To analyse a new document, run the server without this launcher
# (or export VOINEY_LAB_ANALYSIS_MODEL after it) so the call is a deliberate act.
export VOINEY_LAB_ANALYSIS_MODEL=""
export VOINEY_LAB_PROTOCOL_ENABLED="true"
export VOINEY_LAB_PROTOCOL_DATA_DIR="$PROTOCOL_DATA_DIR"
export VOINEY_LAB_MOSS_ENABLED="false"
export VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED="true"
export VOINEY_LAB_EXPERIMENT_REPORT_DB="$PROTOCOL_DATA_DIR/experiment_reports.sqlite"
export VOINEY_LAB_WORKSPACE_ENABLED="true"
export VOINEY_LAB_WORKSPACE_DATA_DIR="$PROTOCOL_DATA_DIR/workspace"
export VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED="true"
export VOINEY_LAB_EXTERNAL_REFERENCE_DOMAIN_PROFILE="open"
export VOINEY_LAB_SUPPLEMENTAL_MODEL="grok-4.6"
export VOINEY_LAB_EXTERNAL_REFERENCE_TIMEOUT_SECONDS="90"
export VOINEY_LAB_EXTERNAL_REFERENCE_CONNECT_TIMEOUT_SECONDS="5"
export VOINEY_LAB_EXTERNAL_REFERENCE_READ_TIMEOUT_SECONDS="90"
export VOINEY_LAB_EXTERNAL_REFERENCE_CACHE_TTL_SECONDS="900"
export VOINEY_LAB_EXTERNAL_REFERENCE_MAX_CITATIONS="5"
export VOINEY_LAB_EXTERNAL_REFERENCE_ENRICHMENT_BUDGET_SECONDS="4"
# PROJECT-ENGINEERING: three bounded read-only planning/answering roles are
# available conditionally; course-explicit state/tool guardrails remain server
# enforced. Report Brain is a separate async derivation path and is not part of
# the latency-critical Answer/Source/Visual start() fan-out.
export VOINEY_LAB_MULTI_BRAIN_ENABLED="true"
export VOINEY_LAB_ANSWER_MODEL="grok-4.6"
export VOINEY_LAB_ANSWER_BRAIN_PRIMARY_BUDGET_SECONDS="1.25"
export VOINEY_LAB_ANSWER_BRAIN_TIMEOUT_SECONDS="8"
export VOINEY_LAB_PLANNER_BRAIN_TIMEOUT_SECONDS="6"
# CLASS-EXPLICIT: model prose cannot gain workflow or evidence authority.
# PROJECT-ENGINEERING: this development launcher enables one bounded Grok-only
# background tier; production/operator launchers may keep the feature disabled.
export VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED="true"
export VOINEY_LAB_SUPPLEMENTAL_MODEL="grok-4.6"
export VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_TIMEOUT_SECONDS="8"
export VOINEY_LAB_WEB_VISUAL_SEARCH_ENABLED="true"
export VOINEY_LAB_GENERATED_VISUALS_ENABLED="true"
export VOINEY_LAB_CASCADE_BARGE_IN_PREFIX_MS="800"
# Raw microphone evidence remains off unless the operator explicitly opts in
# before startup with VOINEY_LAB_STT_DIAGNOSTICS_ENABLED=true. Any
# configured diagnostic directory must remain below data/runtime and is ignored.

if [[ "$TEST_MODE" == "true" ]]; then
  # Development test mode. The server only honours it outside an operational
  # usage scope, so the scope is set here too; it never changes a readiness
  # verdict, it only lets an analysed protocol be run with gates outstanding.
  export VOINEY_LAB_USAGE_SCOPE="demo"
  export VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES="true"
  echo
  echo "!!! TEST MODE ON (--test-mode) !!!"
  echo "  VOINEY_LAB_USAGE_SCOPE=demo"
  echo "  VOINEY_LAB_TEST_MODE_SKIP_READINESS_GATES=true"
  echo "  Analysed protocols can be activated and run with readiness gates"
  echo "  outstanding. Development only; not for real experiments."
fi

echo
echo "=== Non-secret capability check ==="
python -B - <<'PY'
import os
from pathlib import Path

from dotenv import load_dotenv

from voiney_lab.external_references import (
    ExternalReferenceSettings,
    SupplementalKnowledgeSettings,
)
from voiney_lab.generated_visuals import GeneratedVisualSettings
from voiney_lab.multi_brain import MultiBrainSettings
from voiney_lab.web_visuals import WebVisualSettings

load_dotenv(Path.cwd() / ".env", override=False)
references = ExternalReferenceSettings.from_environment()
web_images = WebVisualSettings.from_environment(references)
generated = GeneratedVisualSettings.from_environment()
supplemental = SupplementalKnowledgeSettings.from_environment()
multi_brain = MultiBrainSettings.from_environment()
if references.enabled and not bool(os.environ.get("XAI_API_KEY")):
    raise SystemExit("[ERROR] XAI_API_KEY is not configured for enabled Candidate A research")
print("authoritative_web_search:", "enabled" if references.enabled else "disabled")
print("external_search_model:", references.model if references.enabled else "disabled")
print("external_search_profile:", references.domain_profile or "custom")
print("external_search_open_mode:", "true" if (references.domain_profile == "open" or not references.allowed_domains) else "false")
print("external_search_allowed_domain_count:", len(references.allowed_domains))
print("external_search_timeout_seconds:", references.timeout_seconds)
print("external_search_connect_timeout_seconds:", references.connect_timeout_seconds)
print("external_search_read_timeout_seconds:", references.read_timeout_seconds)
print("external_search_image_search_policy:", "on_visual_request")
print("supplemental_model_knowledge:", "enabled" if supplemental.enabled else "disabled")
print("hybrid_multi_brain:", "enabled" if multi_brain.enabled else "disabled")
print("primary_answer_budget_seconds:", multi_brain.primary_answer_budget_seconds)
print("authority_profile:", references.domain_profile or "custom")
print("allowed_domain_count:", len(references.allowed_domains))
print("web_image_search:", "enabled" if web_images.enabled else "disabled")
print("generated_visuals:", "enabled" if generated.enabled else "disabled")
print("experiment_reports: enabled")
print("barge_in_prefix_ms:", os.environ["VOINEY_LAB_CASCADE_BARGE_IN_PREFIX_MS"])
PY

echo
echo "=== Effective Candidate A paths ==="
echo "FIXTURE    = $VOINEY_LAB_CURATED_PROTOCOL_FIXTURE"
echo "PROVENANCE = $VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE"
echo "SOURCE_PDF = $VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF"
echo "CATALOG    = $VOINEY_LAB_PROTOCOL_DATA_DIR/protocol_workspace.sqlite"
echo "ASSET_ROOT = $VOINEY_LAB_PROTOCOL_DATA_DIR/objects/sha256"
echo "REPORT_DB  = $VOINEY_LAB_EXPERIMENT_REPORT_DB"

echo
echo "=== Loading curated fixture ==="

python -B - <<'PY'
import os
from pathlib import Path

from voiney_lab.curated_protocol import load_curated_protocol_fixture
from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
from voiney_lab.experiment_protocol_store import initialize_protocol_store
from voiney_lab.protocol_catalog import ProtocolCatalog

fixture = load_curated_protocol_fixture(
    Path(os.environ["VOINEY_LAB_CURATED_PROTOCOL_FIXTURE"]),
    Path(os.environ["VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE"]),
    Path(os.environ["VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF"]),
)

settings = ProtocolPersistenceSettings.from_environment()
store = initialize_protocol_store(settings)
try:
    bootstrap = ProtocolCatalog(store).bootstrap_development_fixture(fixture)
finally:
    store.close()

print("[OK] LOAD_OK")
print("protocol_id:", fixture.protocol_id)
print("revision_id:", fixture.revision_id)
print("title:", fixture.title)
print("steps:", len(fixture.steps))
print("status:", fixture.status)
print("materialized:", "existing" if bootstrap.deduplicated else "created")
PY

if [[ "$BOOTSTRAP_ONLY" == "true" ]]; then
  echo "[OK] Candidate A bootstrap complete; server not started"
  exit 0
fi

echo
echo "=== Starting Voiney Lab ==="
echo "HOST = $HOST"
echo "PORT = $PORT"

exec python -B -m uvicorn \
  voiney_lab.server:app \
  --host "$HOST" \
  --port "$PORT"
