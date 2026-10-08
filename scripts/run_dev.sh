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
CHECK_ONLY=false
while [[ "$#" -ne 0 ]]; do
  case "$1" in
    --bootstrap-only)
      BOOTSTRAP_ONLY=true
      shift
      ;;
    --check-only)
      # Print the effective settings and run the start-up checks, then stop
      # before anything is read from or written to data/runtime.
      CHECK_ONLY=true
      shift
      ;;
    *)
      echo "usage: $0 [--bootstrap-only] [--check-only]"
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


# --- Fixed by this launcher ----------------------------------------------------
# The fixture it verifies below, the data root it keeps apart from every other
# run, and the safety settings. These are not taken from a .env.
export VOINEY_LAB_CURATED_PROTOCOL_FIXTURE="$FIXTURE"
export VOINEY_LAB_CURATED_PROTOCOL_PROVENANCE="$PROVENANCE"
export VOINEY_LAB_CURATED_PROTOCOL_SOURCE_PDF="$SOURCE_PDF"
# The analysis model is not touched here (lane PA, human decision 2 as changed
# on 2026-10-06). Registering a PDF in the browser starts its analysis with the
# analysis role a person chose (VOINEY_LAB_ANALYSIS_PROVIDER, _MODEL and
# _REASONING in the shell or the .env). The same PDF uploaded again never calls
# an analysis that already ended; a failed one is called again only when a
# person presses "분석 다시 시도".
export VOINEY_LAB_PROTOCOL_DATA_DIR="$PROTOCOL_DATA_DIR"
export VOINEY_LAB_EXPERIMENT_REPORT_DB="$PROTOCOL_DATA_DIR/experiment_reports.sqlite"
export VOINEY_LAB_WORKSPACE_DATA_DIR="$PROTOCOL_DATA_DIR/workspace"
export VOINEY_LAB_MOSS_ENABLED="false"

# --- Defaults: a value set in the shell or the .env wins ------------------------
# Decision 1 of lane XO (2026-10-05). Each name below is exported only when a
# person has not set it already, in the shell or in the repository .env. The
# launcher names no model: every role keeps model_providers' default unless
# the .env chooses one. The four features only xAI provides (external
# reference web search, web image search, generated images, and semantic
# intent, which is off unless set) default to off, so the server starts
# without XAI_API_KEY; turning one on in the .env needs the key.
# PROJECT-ENGINEERING: three bounded read-only planning/answering roles are
# available conditionally; course-explicit state/tool guardrails remain server
# enforced. Report Brain is a separate async derivation path and is not part of
# the latency-critical Answer/Source/Visual start() fan-out.
# CLASS-EXPLICIT: model prose cannot gain workflow or evidence authority.
# Lane F, decision 1 (2026-10-06): the voice turn's Answer role is off by
# default. Only an answer back within 1.25 s is used, and none of the measured
# models made it (0/57), so the call cost and added nothing. Source and Visual
# stay on with the multi-brain switch; step translation follows the
# translation role, not this switch. Set it to true to turn the role back on.
eval "$(python -B -m voiney_lab.configuration --launcher-defaults "$ROOT/.env" \
  VOINEY_LAB_PROTOCOL_ENABLED=true \
  VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED=true \
  VOINEY_LAB_WORKSPACE_ENABLED=true \
  VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED=false \
  VOINEY_LAB_WEB_VISUAL_SEARCH_ENABLED=false \
  VOINEY_LAB_GENERATED_VISUALS_ENABLED=false \
  VOINEY_LAB_EXTERNAL_REFERENCE_DOMAIN_PROFILE=open \
  VOINEY_LAB_EXTERNAL_REFERENCE_TIMEOUT_SECONDS=90 \
  VOINEY_LAB_EXTERNAL_REFERENCE_CONNECT_TIMEOUT_SECONDS=5 \
  VOINEY_LAB_EXTERNAL_REFERENCE_READ_TIMEOUT_SECONDS=90 \
  VOINEY_LAB_EXTERNAL_REFERENCE_CACHE_TTL_SECONDS=900 \
  VOINEY_LAB_EXTERNAL_REFERENCE_MAX_CITATIONS=5 \
  VOINEY_LAB_EXTERNAL_REFERENCE_ENRICHMENT_BUDGET_SECONDS=4 \
  VOINEY_LAB_MULTI_BRAIN_ENABLED=true \
  VOINEY_LAB_ANSWER_BRAIN_ENABLED=false \
  VOINEY_LAB_ANSWER_BRAIN_PRIMARY_BUDGET_SECONDS=1.25 \
  VOINEY_LAB_ANSWER_BRAIN_TIMEOUT_SECONDS=8 \
  VOINEY_LAB_PLANNER_BRAIN_TIMEOUT_SECONDS=6 \
  VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED=true \
  VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_TIMEOUT_SECONDS=8 \
  VOINEY_LAB_CASCADE_BARGE_IN_PREFIX_MS=800)"
# Raw microphone evidence remains off unless the operator explicitly opts in
# before startup with VOINEY_LAB_STT_DIAGNOSTICS_ENABLED=true. Any
# configured diagnostic directory must remain below data/runtime and is ignored.

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
from voiney_lab.model_providers import ROLES, RoleModel
from voiney_lab.multi_brain import MultiBrainSettings
from voiney_lab.web_visuals import WebVisualSettings

load_dotenv(Path.cwd() / ".env", override=False)
references = ExternalReferenceSettings.from_environment()
web_images = WebVisualSettings.from_environment(references)
generated = GeneratedVisualSettings.from_environment()
supplemental = SupplementalKnowledgeSettings.from_environment()
multi_brain = MultiBrainSettings.from_environment()
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
print("answer_brain:", "enabled" if multi_brain.answer_brain_enabled else "disabled")
print("step_translation:", "enabled" if multi_brain.translation_enabled else "disabled")
print("primary_answer_budget_seconds:", multi_brain.primary_answer_budget_seconds)
print("authority_profile:", references.domain_profile or "custom")
print("allowed_domain_count:", len(references.allowed_domains))
print("web_image_search:", "enabled" if web_images.enabled else "disabled")
print("generated_visuals:", "enabled" if generated.enabled else "disabled")
print("experiment_reports:", os.environ.get("VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED", "false"))
print("barge_in_prefix_ms:", os.environ.get("VOINEY_LAB_CASCADE_BARGE_IN_PREFIX_MS", "800"))
for role in ROLES:
    chosen = RoleModel.from_environment(role)
    print(f"{role}_role:", chosen.provider, chosen.model or "(the caller's default)")
PY
# An xAI-only feature switched on without XAI_API_KEY is refused here, by
# name, and again by the server at start-up (decision 2 of lane XO).
python -B -m voiney_lab.configuration --refuse-xai-only-without-key "$ROOT/.env" || exit 1

if [[ "$CHECK_ONLY" == "true" ]]; then
  echo
  echo "[OK] --check-only: settings printed; nothing read from or written to data/runtime"
  exit 0
fi

echo
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
