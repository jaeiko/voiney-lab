#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/student/voice-ai-course/voice-workflow-agent"

FIXTURE="$ROOT/data/development_protocols/candidate_a_curated_analysis.json"
PROVENANCE="$ROOT/data/development_protocols/candidate_a_curated_analysis.provenance.json"
SOURCE_PDF="/home/student/protocol-test-files/in-gel-digestion.pdf"
PROTOCOL_DATA_DIR="$ROOT/data/runtime/candidate-a-live-acceptance"

EXPECTED_PDF_SHA256="63d81102fb644fca21e1c2296b566987756f2964ece06758fe52c73ba9c00bd9"

BOOTSTRAP_ONLY=false
if [[ "${1:-}" == "--bootstrap-only" ]]; then
  BOOTSTRAP_ONLY=true
  shift
fi
if [[ "$#" -ne 0 ]]; then
  echo "usage: $0 [--bootstrap-only]"
  exit 2
fi

cd "$ROOT"

if [[ ! -f ".venv/bin/activate" ]]; then
  echo "[ERROR] venv not found: $ROOT/.venv"
  exit 1
fi

source .venv/bin/activate

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

export VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_FIXTURE="$FIXTURE"
export VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_PROVENANCE="$PROVENANCE"
export VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_SOURCE_PDF="$SOURCE_PDF"
export VOICE_WORKFLOW_AGENT_PROTOCOL_ENABLED="true"
export VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR="$PROTOCOL_DATA_DIR"
export VOICE_WORKFLOW_AGENT_MOSS_ENABLED="false"
export VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED="true"
export VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB="$PROTOCOL_DATA_DIR/experiment_reports.sqlite"
export EXTERNAL_REFERENCES_ENABLED="true"
export EXTERNAL_REFERENCE_DOMAIN_PROFILE="candidate_a"
export EXTERNAL_REFERENCE_MODEL="grok-4.6"
export EXTERNAL_REFERENCE_TIMEOUT_SECONDS="20"
export EXTERNAL_REFERENCE_CONNECT_TIMEOUT_SECONDS="3"
export EXTERNAL_REFERENCE_READ_TIMEOUT_SECONDS="15"
export EXTERNAL_REFERENCE_CACHE_TTL_SECONDS="900"
export EXTERNAL_REFERENCE_MAX_CITATIONS="5"
export EXTERNAL_REFERENCE_ENRICHMENT_BUDGET_SECONDS="4"
# PROJECT-ENGINEERING: three bounded read-only planning/answering roles are
# available conditionally; course-explicit state/tool guardrails remain server
# enforced. Report Brain is a separate async derivation path and is not part of
# the latency-critical Answer/Source/Visual start() fan-out.
export VOICE_WORKFLOW_AGENT_MULTI_BRAIN_ENABLED="true"
export VOICE_WORKFLOW_AGENT_MULTI_BRAIN_MODEL="grok-4.6"
export VOICE_WORKFLOW_AGENT_ANSWER_BRAIN_PRIMARY_BUDGET_SECONDS="1.25"
export VOICE_WORKFLOW_AGENT_ANSWER_BRAIN_TIMEOUT_SECONDS="8"
export VOICE_WORKFLOW_AGENT_PLANNER_BRAIN_TIMEOUT_SECONDS="6"
# CLASS-EXPLICIT: model prose cannot gain workflow or evidence authority.
# PROJECT-ENGINEERING: this development launcher enables one bounded Grok-only
# background tier; production/operator launchers may keep the feature disabled.
export SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED="true"
export SUPPLEMENTAL_MODEL_KNOWLEDGE_MODEL="grok-4.6"
export SUPPLEMENTAL_MODEL_KNOWLEDGE_TIMEOUT_SECONDS="8"
export WEB_VISUAL_SEARCH_ENABLED="true"
export VOICE_WORKFLOW_AGENT_GENERATED_VISUALS_ENABLED="true"
export CASCADE_BARGE_IN_PREFIX_MS="800"
# Raw microphone evidence remains off unless the operator explicitly opts in
# before startup with VOICE_WORKFLOW_AGENT_STT_DIAGNOSTICS_ENABLED=true. Any
# configured diagnostic directory must remain below data/runtime and is ignored.

echo
echo "=== Non-secret capability check ==="
python -B - <<'PY'
import os
from pathlib import Path

from dotenv import load_dotenv

from voice_workflow_agent.external_references import (
    ExternalReferenceSettings,
    SupplementalKnowledgeSettings,
)
from voice_workflow_agent.generated_visuals import GeneratedVisualSettings
from voice_workflow_agent.multi_brain import MultiBrainSettings
from voice_workflow_agent.web_visuals import WebVisualSettings

load_dotenv(Path.cwd() / ".env", override=False)
references = ExternalReferenceSettings.from_environment()
web_images = WebVisualSettings.from_environment(references)
generated = GeneratedVisualSettings.from_environment()
supplemental = SupplementalKnowledgeSettings.from_environment()
multi_brain = MultiBrainSettings.from_environment()
if references.enabled and not bool(os.environ.get("XAI_API_KEY")):
    raise SystemExit("[ERROR] XAI_API_KEY is not configured for enabled Candidate A research")
print("authoritative_web_search:", "enabled" if references.enabled else "disabled")
print("supplemental_model_knowledge:", "enabled" if supplemental.enabled else "disabled")
print("hybrid_multi_brain:", "enabled" if multi_brain.enabled else "disabled")
print("primary_answer_budget_seconds:", multi_brain.primary_answer_budget_seconds)
print("authority_profile:", references.domain_profile or "custom")
print("allowed_domain_count:", len(references.allowed_domains))
print("web_image_search:", "enabled" if web_images.enabled else "disabled")
print("generated_visuals:", "enabled" if generated.enabled else "disabled")
print("experiment_reports: enabled")
print("barge_in_prefix_ms:", os.environ["CASCADE_BARGE_IN_PREFIX_MS"])
PY

echo
echo "=== Effective Candidate A paths ==="
echo "FIXTURE    = $VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_FIXTURE"
echo "PROVENANCE = $VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_PROVENANCE"
echo "SOURCE_PDF = $VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_SOURCE_PDF"
echo "CATALOG    = $VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR/protocol_workspace.sqlite"
echo "ASSET_ROOT = $VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR/objects/sha256"
echo "REPORT_DB  = $VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORT_DB"

echo
echo "=== Loading curated fixture ==="

python -B - <<'PY'
import os
from pathlib import Path

from voice_workflow_agent.curated_protocol import load_curated_protocol_fixture
from voice_workflow_agent.experiment_protocol_config import ProtocolPersistenceSettings
from voice_workflow_agent.experiment_protocol_store import initialize_protocol_store
from voice_workflow_agent.protocol_catalog import ProtocolCatalog

fixture = load_curated_protocol_fixture(
    Path(os.environ["VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_FIXTURE"]),
    Path(os.environ["VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_PROVENANCE"]),
    Path(os.environ["VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_SOURCE_PDF"]),
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
echo "=== Starting Voice Workflow Agent ==="

exec python -B -m uvicorn \
  voice_workflow_agent.server:app \
  --host 0.0.0.0 \
  --port 8000
