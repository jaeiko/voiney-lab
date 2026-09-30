#!/usr/bin/env bash
# Compatibility shim: this launcher was renamed to scripts/run_dev.sh.
#
# Kept so bookmarks, notes, and older docs keep working. It adds nothing of
# its own - it prints the new name and hands every argument straight over.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[NOTE] scripts/run_candidate_a.sh was renamed to scripts/run_dev.sh."
echo "[NOTE] Running scripts/run_dev.sh instead; please use the new name."

exec "$ROOT/scripts/run_dev.sh" "$@"
