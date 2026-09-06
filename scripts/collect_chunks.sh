#!/usr/bin/env bash
#
# Collect one document's chunks, one provider call at a time.
#
# STEP 31 could not run the collection itself: this environment's permission
# classifier refused the command, and a provider call spends real budget and
# leaves the machine, so it was not worked around. The collection was run by
# hand instead, and by the time anyone read the result two chunks had been
# refused and the reason codes existed only in a terminal.
#
# So this exists to make the manual path the reliable one rather than the
# improvised one. It stops on the first refusal instead of burning the rest of
# the budget on the same mistake, and every run writes its report to
# data/development_cache/provider_diagnostics/ so a refusal can still be read
# tomorrow.
#
# A chunk that already passed is served from cache and costs nothing, so
# re-running after a fix only pays for what actually failed.
#
#   scripts/collect_chunks.sh <source.pdf> [chunk ...]
#
# With no chunk numbers it does 0 1 2 3 4.
set -u -o pipefail

SOURCE="${1:-}"
if [ -z "$SOURCE" ] || [ ! -f "$SOURCE" ]; then
  echo "usage: $0 <source.pdf> [chunk ...]" >&2
  exit 2
fi
shift
CHUNKS=("$@")
if [ ${#CHUNKS[@]} -eq 0 ]; then CHUNKS=(0 1 2 3 4); fi

# The repo .env sets flags that change behaviour under test; forcing them off
# here keeps a collection run comparable with the documented baseline. It does
# not read or print .env itself.
export VOICE_WORKFLOW_AGENT_MOSS_ENABLED=false
export VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED=false
export VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED=false

if [ -f .venv/bin/activate ]; then . .venv/bin/activate; fi

spent=0
for chunk in "${CHUNKS[@]}"; do
  echo "=== chunk ${chunk} ===" >&2
  python scripts/diagnose_provider_chunk.py \
    "$SOURCE" --chunk "$chunk" --budget 1 --execute
  status=$?
  spent=$((spent + 1))
  if [ $status -ne 0 ]; then
    echo "chunk ${chunk} exited ${status}; stopping." >&2
    echo "calls attempted: ${spent}" >&2
    exit $status
  fi
done
echo "calls attempted: ${spent}" >&2
echo "reports: data/development_cache/provider_diagnostics/" >&2
