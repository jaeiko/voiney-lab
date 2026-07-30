# Moss in-memory retrieval

Voice Workflow Agent can optionally use [Moss](https://github.com/usemoss/moss) to
rerank approved safety-document sections in memory. This integration is designed
for the voice path, where retrieval delay is directly heard as silence.

## Safety boundary

Moss is not the source of truth and cannot make an unapproved section answerable.
The request always passes through the existing SQLite policy gates first:

1. exact product identity or approved alias
2. requested topic route
3. trusted usage scope and facility
4. approval and active state
5. review due date and version consistency
6. requested language and reviewed translation state

Only the candidate section IDs that pass every gate are included in the Moss
metadata filter. Moss ranks those candidates; it cannot introduce another
document, facility, language, version, or section. Facility SOP priority over SDS
is also preserved.

If Moss is disabled, not installed, not ready, times out, returns unknown IDs, or
raises an error, Voice Workflow Agent returns the existing deterministic SQLite ordering.
Server startup and safety search therefore do not depend on Moss availability.

## Data-flow

```text
reviewed catalog SQLite
  ├─ explicit sync command ──> Moss Cloud index
  └─ runtime policy gates ──> approved candidate IDs

server startup ──> load Moss index into server memory

voice query
  ──> SQLite policy gates
  ──> Moss hybrid rerank with candidate-ID filter
  ──> at most three verbatim approved sections
```

Index creation and updates upload eligible section text to Moss Cloud. Runtime
queries run against the loaded in-memory index, but this does not make the initial
upload suitable for confidential documents. Use only data that the organization
has approved for that external service. `operational` content requires an explicit
command-line acknowledgement and is not enabled in the runtime by default.

## 1. Install the optional SDK

```bash
cd ~/voice-ai-course/voice-workflow-agent
source .venv/bin/activate
python -m pip install -e '.[moss]'
```

## 2. Add Moss credentials locally

Create a project in the Moss portal and place its values only in
`voice-workflow-agent/.env`:

```dotenv
MOSS_PROJECT_ID=replace-with-moss-project-id
MOSS_PROJECT_KEY=replace-with-moss-project-key
MOSS_INDEX_NAME=voice_workflow_agent-approved-safety
MOSS_MODEL_ID=moss-minilm
```

`.env` is ignored by Git. Do not put a key in a manifest, shell history, commit,
test fixture, screenshot, or report.

## Reproducible fictional demo

The repository includes a separate `demo`-scope catalog containing only
`FICTIONAL NON-OPERATIONAL` records. It is independent of the Phase 6
`test_only` Procedure fixture.

```bash
demo_dir=$(mktemp -d)
python scripts/setup_moss_demo.py --output-dir "$demo_dir"

export VOICE_WORKFLOW_AGENT_SAFETY_CATALOG="$demo_dir/moss_demo_catalog.sqlite"
export VOICE_WORKFLOW_AGENT_FACILITY_ID="MOSS-DEMO-FACILITY"
export VOICE_WORKFLOW_AGENT_USAGE_SCOPE="demo"
export VOICE_WORKFLOW_AGENT_SESSION_LANGUAGE="ko"
export VOICE_WORKFLOW_AGENT_ALLOWED_LANGUAGES="ko"
unset VOICE_WORKFLOW_AGENT_PROCEDURE_CATALOG VOICE_WORKFLOW_AGENT_PROCEDURE_STORE
```

Preview and then create the non-sensitive demo index:

```bash
python scripts/sync_moss_index.py \
  --db "$VOICE_WORKFLOW_AGENT_SAFETY_CATALOG" \
  --usage-scope demo \
  --index-name "$MOSS_INDEX_NAME" \
  --dry-run

python scripts/sync_moss_index.py \
  --db "$VOICE_WORKFLOW_AGENT_SAFETY_CATALOG" \
  --usage-scope demo \
  --index-name "$MOSS_INDEX_NAME"
```

Enable Moss, restart Uvicorn, and ask:

```text
모스 가상 용제 누출에 관한 승인 데모 자료를 찾아 줘.
```

The Tool must return only the fictional facility SOP and SDS section. The terminal
must show `backend=moss`. Do not use the returned demo text for real work.

## 3. Preview the exact export

For non-sensitive demo data:

```bash
python scripts/sync_moss_index.py \
  --db "$VOICE_WORKFLOW_AGENT_SAFETY_CATALOG" \
  --usage-scope demo \
  --index-name "$MOSS_INDEX_NAME" \
  --dry-run
```

The dry run validates the catalog and prints only the eligible section count. It
does not import the Moss SDK or make a network call.

For an `operational` catalog, the sync command refuses to upload unless the operator
explicitly adds `--allow-sensitive-scope`:

```bash
python scripts/sync_moss_index.py \
  --db "$VOICE_WORKFLOW_AGENT_SAFETY_CATALOG" \
  --usage-scope operational \
  --index-name "$MOSS_INDEX_NAME" \
  --allow-sensitive-scope \
  --dry-run
```

Remove `--dry-run` only after reviewing the selected scope and approval policy.
The command creates a missing index or upserts the current approved sections and
removes IDs that are no longer present in that scope. Use a separate Moss index
name for each usage scope. The command refuses to update or delete from an existing
index containing documents that are not Voice Workflow Agent-managed records for the exact
requested scope.

## 4. Enable the runtime

For demo or reference-only data, add:

```dotenv
VOICE_WORKFLOW_AGENT_MOSS_ENABLED=true
VOICE_WORKFLOW_AGENT_MOSS_ALLOWED_SCOPES=demo,reference_only
VOICE_WORKFLOW_AGENT_MOSS_ALPHA=0.65
VOICE_WORKFLOW_AGENT_MOSS_CANDIDATE_LIMIT=64
VOICE_WORKFLOW_AGENT_MOSS_QUERY_TIMEOUT_MS=250
VOICE_WORKFLOW_AGENT_MOSS_LOAD_TIMEOUT_SECONDS=60
VOICE_WORKFLOW_AGENT_MOSS_AUTO_REFRESH=false
VOICE_WORKFLOW_AGENT_MOSS_REFRESH_SECONDS=600
```

`VOICE_WORKFLOW_AGENT_MOSS_ALLOWED_SCOPES`는 쉼표로 구분하며 공백과 대소문자를
정규화하고 중복을 제거한다. 허용값은 `operational`, `demo`,
`reference_only`뿐이며 빈 집합이나 알 수 없는 값은 거부하고 SQLite
fallback을 유지한다.

To use a deliberately approved operational index, the operator must separately
change:

```dotenv
VOICE_WORKFLOW_AGENT_MOSS_ALLOWED_SCOPES=operational
```

Restart Uvicorn after changing the index or runtime configuration. On successful
startup the terminal prints:

```text
Moss index loaded in memory: voice_workflow_agent-approved-safety
```

A successful search prints an `approved retrieval backend=moss` log with elapsed
milliseconds and candidate count. `backend=sqlite_fallback` means the query stayed
safe and completed through the original deterministic ordering.

## Tuning

- `moss-minilm` is the speed-first model used for the voice demo.
- `VOICE_WORKFLOW_AGENT_MOSS_ALPHA=0.65` keeps both semantic and keyword signal.
- Candidate count is bounded to 64 and result count remains three.
- Query timeout is 250 ms so a stalled optional backend cannot hold the voice turn.
- Automatic index refresh is off by default for a stable demo. Enable it only when
  controlled live index updates are required.
