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

echo
echo "=== Effective Candidate A paths ==="
echo "FIXTURE    = $VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_FIXTURE"
echo "PROVENANCE = $VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_PROVENANCE"
echo "SOURCE_PDF = $VOICE_WORKFLOW_AGENT_CURATED_PROTOCOL_SOURCE_PDF"
echo "CATALOG    = $VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR/protocol_workspace.sqlite"
echo "ASSET_ROOT = $VOICE_WORKFLOW_AGENT_PROTOCOL_DATA_DIR/objects/sha256"

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
