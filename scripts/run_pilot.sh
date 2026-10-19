#!/usr/bin/env bash
# Controlled-pilot launcher.
#
# Unlike scripts/run_dev.sh this loads no development fixture, keeps its own
# data root, and turns every out-of-source-document feature off. Nothing here
# weakens the execution rule or a source check: it only decides which
# optional subsystems are configured on.
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
# Human decision of 2026-10-19 (lane FX4, decision 4): the pilot signs in.
# Accounts (VOINEY_LAB_AUTH_MODE=accounts) unless a person chooses the
# development identity here, on the command line.
PILOT_AUTH_MODE="accounts"
while [[ "$#" -ne 0 ]]; do
  case "$1" in
    --check-only)
      CHECK_ONLY=true
      shift
      ;;
    --dev-identity)
      PILOT_AUTH_MODE="dev"
      shift
      ;;
    *)
      echo "usage: $0 [--check-only] [--dev-identity]"
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
# STT diagnostic recordings (off unless VOINEY_LAB_STT_DIAGNOSTICS_ENABLED) go
# inside the pilot folder, never to the development default, so pilot audio and
# transcripts are found -- and deleted -- with the rest of the pilot's data
# (scripts/pilot_data_delete.py) and are not mixed with development data.
if [[ -n "${VOINEY_LAB_STT_DIAGNOSTIC_DIR:-}" && "$VOINEY_LAB_STT_DIAGNOSTIC_DIR" != "$PILOT_DATA_DIR/stt-diagnostics" ]]; then
  echo "[WARN] VOINEY_LAB_STT_DIAGNOSTIC_DIR was set in the environment. The pilot ignores it and"
  echo "[WARN] uses $PILOT_DATA_DIR/stt-diagnostics."
fi
export VOINEY_LAB_STT_DIAGNOSTIC_DIR="$PILOT_DATA_DIR/stt-diagnostics"

# --- Off unless a person asks for it -----------------------------------------
# The general explanation ("AI 일반 지식") reaches outside the approved source
# documents. It keeps a value the operator set in the shell or wrote in the
# repository .env (decision 1 of lane XO, 2026-10-05), so turning it on is a
# deliberate act. (The xAI web reference search that used to sit beside it was
# deleted in lane DOC3; a feature only xAI provides, switched on without
# XAI_API_KEY, would still be refused below, by name.)
eval "$(python -B -m voiney_lab.configuration --launcher-defaults "$ROOT/.env" \
  VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED=false)"

# --- Approved safety documents: decided here, never by a .env ---------------
# Two runs, told apart by one fact: whether an approved safety catalog is at
# the fixed path below.
#
# With a catalog: reference_only. operational needs OIDC, which the pilot does
# not have yet: without it the identity resolver refuses every /api and /ws
# request. demo and test_only need at least one document of that scope in the
# catalog, and every such document is demo material, which the check below
# refuses. reference_only is the scope left: development identity, approved
# non-demo documents, and a model told the material is non-operational.
#
# Without one: source_protocol_only (human decision of 2026-10-13, "B안"). The
# pilot starts on the safety statements of the uploaded PDF alone, no catalog
# is required or read, and the screen and the record say so in the server's
# words: "기관 안전 문서 없음 · 원문 안전 주의만". Nothing is hidden: the banner
# below says it too. A catalog file that is there but unusable (a demo
# document in it, no approved reference_only document, not a catalog) is never
# taken for "no catalog": it refuses, and moving the file away is the way to
# start without institution documents.
PILOT_SAFETY_CATALOG_PATH="$PILOT_DATA_DIR/approved_safety_catalog.sqlite"
if [[ -e "$PILOT_SAFETY_CATALOG_PATH" || -L "$PILOT_SAFETY_CATALOG_PATH" ]]; then
  PILOT_USAGE_SCOPE="reference_only"
  PILOT_SAFETY_CATALOG="$PILOT_SAFETY_CATALOG_PATH"
else
  PILOT_USAGE_SCOPE="source_protocol_only"
  PILOT_SAFETY_CATALOG=""
fi
NO_INSTITUTION_DOCUMENTS="기관 안전 문서 없음 · 원문 안전 주의만"
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
# never reach the safety panel of a pilot run. Only with a catalog.
SAFETY_DEMO_DOCUMENTS="-"
SAFETY_SCOPE_DOCUMENTS="-"
SAFETY_REFUSAL=""
if [[ "$PILOT_USAGE_SCOPE" == "source_protocol_only" ]]; then
  :
elif [[ ! -f "$VOINEY_LAB_SAFETY_CATALOG" ]]; then
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

# --- Sign-in: decided here, never by a .env ----------------------------------
# The shell's or the .env's VOINEY_LAB_AUTH_MODE does not choose the door: it
# is the launcher's (accounts), or dev by --dev-identity. What the server will
# read for the admin token -- the shell's value, else the .env's (server.py
# loads the .env without overriding the shell) -- is looked at, never printed.
read -r SET_AUTH_MODE ADMIN_TOKEN_STATE < <(python -B - "$ROOT/.env" <<'PY' 2>/dev/null || echo "- empty"
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

written = dotenv_values(Path(sys.argv[1])) if Path(sys.argv[1]).is_file() else {}


def value(name: str) -> str:
    return (os.environ[name] if name in os.environ else written.get(name) or "").strip()


print(value("VOINEY_LAB_AUTH_MODE").casefold() or "-",
      "set" if value("VOINEY_LAB_ADMIN_TOKEN") else "empty")
PY
)
if [[ "$SET_AUTH_MODE" != "-" && "$SET_AUTH_MODE" != "$PILOT_AUTH_MODE" ]]; then
  echo "[WARN] VOINEY_LAB_AUTH_MODE was set to $SET_AUTH_MODE in the environment or .env."
  echo "[WARN] The pilot ignores it and uses $PILOT_AUTH_MODE (--dev-identity chooses dev)."
fi
export VOINEY_LAB_AUTH_MODE="$PILOT_AUTH_MODE"

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
echo "STT_DIAG   = $VOINEY_LAB_STT_DIAGNOSTIC_DIR  (recordings only when VOINEY_LAB_STT_DIAGNOSTICS_ENABLED)"
echo
echo "--- Approved safety documents (fixed by this launcher) ---"
if [[ "$PILOT_USAGE_SCOPE" == "source_protocol_only" ]]; then
  echo "SAFETY_CATALOG = (none: $PILOT_SAFETY_CATALOG_PATH is not there)"
  echo "USAGE_SCOPE    = $VOINEY_LAB_USAGE_SCOPE"
  echo "SAFETY_SCOPE   = $NO_INSTITUTION_DOCUMENTS"
  echo "No institution safety document is searched or shown. The start screen,"
  echo "the safety answers and the experiment record carry the line above."
else
  echo "SAFETY_CATALOG = $VOINEY_LAB_SAFETY_CATALOG"
  echo "USAGE_SCOPE    = $VOINEY_LAB_USAGE_SCOPE"
  echo "demo documents = $SAFETY_DEMO_DOCUMENTS"
  echo "approved active $VOINEY_LAB_USAGE_SCOPE documents = $SAFETY_SCOPE_DOCUMENTS"
fi
echo
echo "--- Features ---"
report_feature() { printf '%-30s %s\n' "$1" "$(feature_state "${2:-}")"; }
report_feature "protocol_catalog:" "$VOINEY_LAB_PROTOCOL_ENABLED"
report_feature "experiment_reports:" "$VOINEY_LAB_EXPERIMENT_REPORTS_ENABLED"
report_feature "workspace:" "$VOINEY_LAB_WORKSPACE_ENABLED"
report_feature "supplemental_model_knowledge:" "$VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED"

echo
echo "--- Sign-in (fixed by this launcher) ---"
echo "AUTH_MODE      = $VOINEY_LAB_AUTH_MODE"
echo "ADMIN_TOKEN    = $ADMIN_TOKEN_STATE"
if [[ "$VOINEY_LAB_AUTH_MODE" == "accounts" ]]; then
  echo "Every participant signs in with an account; invite one with"
  echo "  python scripts/pilot_invite.py   (docs/PILOT_GUIDE.md, section 7)."
else
  echo "[WARN] --dev-identity: no sign-in. Everyone who reaches this server is the one"
  echo "[WARN] development identity, and the record cannot tell participants apart."
fi
if [[ "$(feature_state "$VOINEY_LAB_WORKSPACE_ENABLED")" != "enabled" ]]; then
  echo "[WARN] The workspace is off (VOINEY_LAB_WORKSPACE_ENABLED). Accounts live beside it:"
  echo "[WARN] with accounts on, the server refuses every request until it is on."
fi
if [[ "$ADMIN_TOKEN_STATE" != "set" && "$VOINEY_LAB_AUTH_MODE" == "accounts" ]]; then
  echo "[WARN] VOINEY_LAB_ADMIN_TOKEN is empty. The admin metrics screen opens only for an"
  echo "[WARN] admin account; set a long random value to open it with the token too."
elif [[ "$ADMIN_TOKEN_STATE" != "set" ]]; then
  echo "[WARN] VOINEY_LAB_ADMIN_TOKEN is empty. The admin metrics screen is closed; set a"
  echo "[WARN] long random value to open it with the token."
fi

# --- Word sources need LibreOffice: warn, never refuse -------------------------
# A Word (.docx) source is turned into a PDF by LibreOffice (docs/SOURCE_FORMATS.md).
# Human decision of 2026-10-14: a pilot without it still starts -- a pilot that
# uploads only PDFs does not need it -- and says so here; the server answers a
# .docx upload with docx_conversion_unavailable (503) and GET /readyz reports
# docx_conversion_available. The same finder the server uses, and the .env the
# server will load (without overriding the shell), so the banner is accurate.
# Nothing in this block can stop the start: a failure to look is not a refusal.
OFFICE_CONVERTER_FOUND="$(python -B - "$ROOT/.env" <<'PY' 2>/dev/null || true
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(sys.argv[1]), override=False)
from voiney_lab.experiment_protocol_pdf import find_office_converter

print(find_office_converter() or "")
PY
)"
echo
echo "--- Source formats ---"
if [[ -n "$OFFICE_CONVERTER_FOUND" ]]; then
  echo "word_sources(libreoffice):     available ($OFFICE_CONVERTER_FOUND)"
else
  echo "word_sources(libreoffice):     unavailable"
  echo "[WARN] LibreOffice (soffice) was not found. The pilot starts anyway; a Word (.docx)"
  echo "[WARN] upload is refused with docx_conversion_unavailable until it is installed:"
  echo "[WARN]   sudo apt-get install --no-install-recommends libreoffice-writer-nogui fonts-nanum"
  echo "[WARN] (or set VOINEY_LAB_OFFICE_CONVERTER to its soffice; see docs/SOURCE_FORMATS.md)."
fi

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
  echo "[ERROR] docs/APPROVED_DOCUMENT_OPERATIONS.md) and run this again -- or move"
  echo "[ERROR] that file away to start without institution safety documents"
  echo "[ERROR] (USAGE_SCOPE source_protocol_only: $NO_INSTITUTION_DOCUMENTS)."
  exit 1
fi

if [[ "$CHECK_ONLY" == "true" ]]; then
  echo
  echo "[OK] --check-only: configuration printed; server not started"
  exit 0
fi

mkdir -p "$PILOT_DATA_DIR"
# Marks this folder as pilot data for scripts/pilot_data_delete.py, which
# refuses every folder that is neither this one nor carries the mark.
: > "$PILOT_DATA_DIR/.voiney-pilot-data"

echo
echo "=== Starting Voiney Lab ==="

exec python -B -m uvicorn \
  voiney_lab.server:app \
  --host "$HOST" \
  --port "$PORT"
