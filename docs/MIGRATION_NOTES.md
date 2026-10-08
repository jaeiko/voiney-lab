# Migration Notes

## Commercial workspace schema 1 → 2

Schema 2 adds the persistent experiment-session foundation:

- `experiment_sessions` — mutable lifecycle/recovery projection pinned to an
  exact protocol revision;
- `experiment_session_events` — append-only lifecycle and step history; and
- `experiment_completed_steps` — append-only completed-step identities used for
  safe contiguous recovery.

### Automatic migration

`initialize_workspace_store` detects schema 1 and runs the migration in one
`BEGIN IMMEDIATE` transaction. It creates the new tables and indexes, installs
append-only triggers, replaces the legacy version metadata table, and commits
schema version 2. If any statement fails, initialization raises
`workspace_error` and does not open a partially migrated store.

The legacy `schema_metadata` definition constrained its value to exactly `1`.
For that reason the migration creates `schema_metadata_next`, inserts version 2,
drops only the old metadata table, and renames the replacement. No tenant,
membership, protocol, connector, knowledge, asset, workflow, ELN, or analytics
row is rewritten.

## Commercial workspace schema 2 → 3

Schema 3 adds the durable observation and evidence layer:

- `experiment_observations` — append-only researcher wording associated with
  the current or a completed protocol step, including author, category, capture
  source, and the fixed `observation_only` knowledge boundary; and
- `experiment_evidence` — append-only image/document metadata including the
  original filename, media type, bounded byte size, SHA-256, opaque storage
  reference, and the fixed `not_interpreted` state.

Both tables are tenant/session keyed and have update/delete prevention triggers.
Observation and evidence events are also appended to the existing
`experiment_session_events` ledger. The migration does not copy an observation
into protocol instructions, approved knowledge, or a protocol revision.

`initialize_workspace_store` applies schema 2 → 3 in its own
`BEGIN IMMEDIATE` transaction. A schema-1 database is advanced through 1 → 2
and then 2 → 3 in order. A checked fixture proves that an existing schema-2
session and its exact protocol revision survive the migration.

Evidence file bytes are not embedded in SQLite. New uploads are streamed into a
tenant-bucketed directory under the configured workspace data directory, capped
at 32 MiB, named by content hash, and checked before reuse. Public API and
timeline responses omit the internal storage reference. No OCR, image model, or
document interpretation runs as part of this phase.

## Commercial workspace schema 3 → 4

Schema 4 adds `protocol_adaptation_revisions`, an immutable relationship between
an original lineage revision and a review-required adapted child revision. Each
record stores the tenant/family, exact base and adapted revision IDs, author,
typed change set, and timestamp. Update/delete triggers protect the adaptation
relationship and typed changes.

The adapted protocol content is inserted through the existing immutable
`protocol_lineage_revisions` and review inbox transaction. Allowed change types
are local equipment differences, reagent substitutions, lab notes, and
troubleshooting tips. Equipment and reagent changes require distinct before and
after values plus a rationale. The original revision is never updated.

Approval remains an append-only `protocol_approval_events` action performed by a
reviewer/admin. A development-status source still cannot be approved directly;
only its explicit lab-adaptation child can pass through review to become
available for a new operational session.

### Operator procedure

1. Stop all application processes using the workspace SQLite file.
2. Back up `commercial_workspace.sqlite` together with its WAL/SHM files, if
   present.
3. Start one application instance. Initialization performs the migration before
   serving workspace traffic.
4. Confirm `schema_metadata.schema_version = 5` and exercise tenant login,
   protocol library, connector listing, experiment dashboard reads, a manual
   observation, and a small evidence upload.
5. Retain the backup until the pilot acceptance suite has completed.

Back up the complete configured workspace data directory, not only the SQLite
file, once evidence uploads are enabled. Downgrade is not automatic. A rollback
uses the pre-migration backup; dropping the new tables manually is not
supported.

## Phase 5 connector compatibility

The source-ecosystem phase introduces no database migration. Existing
`connector_configurations`, `connector_sync_cursors`, `connector_webhook_events`,
`protocol_sources`, lineage revisions, and reviewer inbox records remain the
authoritative schema.

Before enabling a live connector after upgrade, verify that its server-side
secret reference resolves, retain the existing allowlisted roots, and perform
one read-only import or change-log poll. Rotating a token does not require
rewriting connector metadata. Never place an OAuth access token, protocols.io
token, GitHub App installation token, or webhook secret directly in SQLite or a
browser request.

## Phase 6 identity compatibility

The identity/workspace phase introduces no schema change. Existing tenant,
principal, membership, and role rows remain valid. Before switching a deployment
to `operational`, configure issuer, audience, HTTPS JWKS URL, and claim mappings;
then create matching active local memberships for the verified tenant subjects.

Development profile identifiers are not migrated into OIDC identities. Keep
demo data isolated, and do not attempt to preserve authority by copying a local
profile role into an operational token or membership automatically.

## Commercial workspace schema 4 → 5

Schema 5 adds nullable `experiment_session_id` provenance columns to existing
`eln_writeback_requests` and append-only `eln_writeback_events`, plus a
tenant/session/time index. New write-backs require and store the durable session
identity. The server verifies that both the ExperimentSession and experiment
report are completed and that their protocol and runtime-revision identities
match before reserving an idempotency key or calling eLabFTW.

The migration preserves legacy write-back request/event rows with a null session
identity; it never invents an association. Those legacy rows remain audit
history but cannot be replayed through the stricter write-back endpoint. A
schema-4 fixture verifies that its existing request survives unchanged.

## Phase 8 OCR compatibility

The OCR/document-intelligence phase changes neither the protocol catalog schema
nor commercial workspace schema 5. OCR request, completion, bounded failure, and
human review records use the existing append-only `protocol_events` ledger and
pin the current immutable protocol revision and source SHA-256. Older catalogs
therefore open without a data rewrite, and text-native protocol behavior is
unchanged.

There is no automatic migration of a prior `ocr_required` row. After upgrade, a
reviewer may explicitly request OCR using a deployment-configured trusted
adapter, compare each page to the preserved PDF, and accept or reject it. An
accepted result only enables a separate structured-analysis request; it does not
backfill an approval or execution record. Rollback leaves the new event types as
unknown append-only history and the original source PDF remains authoritative.

## Phase 9 computational metadata compatibility

The computational workflow phase adds no schema and leaves commercial workspace
schema 5 unchanged. Existing Snakemake/Nextflow families, immutable revisions,
append-only review events, and wet/dry link rows remain readable. New imports
must carry the existing fixed `metadata_only_unexecuted` state and
`execution_supported:false`; new links now resolve a real durable experiment and
verify its wet-lab protocol/source identity instead of accepting a standalone
resource binding.

Legacy link rows are not rewritten or deleted. Their read projection remains
metadata-only. A legacy workflow revision that does not contain the fixed
non-execution metadata is not eligible for a new link and should be re-imported
from its pinned GitHub commit as a new review-required revision. Revoked workflow
revisions remain revoked; re-enabling requires a new immutable source revision.

## Commercial workspace schema 6 → 7

Schema 7 adds two append-only tables for Protocol translations made once per
executable revision (`protocol_translation.py`):

- `protocol_fact_translations`: one row per sentence the step card, the safety
  box and the answers draw -- revision ID, fact key (`<step_id>/<fact_id>`, or
  `protocol/protocol_purpose`), language, SHA-256 of the source sentence,
  translated text, status (`machine` or `reviewed`), mechanical check result
  (`passed` or the refusal reason from `reader_translation_issue`), model,
  model version and creation time. A refused row is kept so it is not
  requested again; it is never shown. Unique per (revision, fact, language,
  source hash, status), so a changed source sentence or a new revision is
  translated anew.
- `protocol_translation_glossaries`: one per (revision, language), the Korean
  form the revision's translation fixes for the words it repeats, as JSON
  entries `{"source", "korean", "keep_english"}`.

The revision ID is the executable catalog or development-fixture revision a
session runs, not a `protocol_lineage_revisions` foreign key, and the rows are
service-level like the protocol catalog (no tenant column). Update and delete
triggers make both tables append-only.

The existing whole-revision `protocol_translations` rows are untouched by the
migration and stay readable through `WorkspaceStore.revision_translations`; a
revision-wide text cannot be split into sentences, so they are not copied into
the new table. A schema-6 fixture with one such row verifies that it survives.

### Operator procedure

Follow the schema 3 → 4 procedure (stop, back up the SQLite file with its
WAL/SHM files, start one instance), then confirm
`schema_metadata.schema_version = 7`. A revision made executable before this
version has no translations until a session opens on it (or it is activated or
approved again), which starts its generation once.

## Environment setting names → `VOINEY_LAB_` (2026-10-04)

This changes configuration, not a store: no database, ledger, table or file
under `data/` is rewritten.

- Every setting the code reads now carries the `VOINEY_LAB_` prefix (decision
  of 2026-10-04). An old prefixed name keeps the rest of its name under the new
  prefix; an old unprefixed application setting (the voice-activity, filler,
  model, external-reference, Moss, SMTP and contact-email settings, among
  others) gains the prefix in front. Nothing else in a name changed.
- The full old → new map is `RENAMED` in `src/voiney_lab/setting_renames.py`,
  the one file in the repository that still spells an old name. The current
  names, with their `.env` area, default and meaning, are the table in
  `src/voiney_lab/setting_names.py`; `tests/test_setting_names.py` keeps the
  table equal to the names the code reads and checks that no old name is left
  anywhere else.
- Kept as they were: `XAI_API_KEY` and `XAI_BASE_URL` (provider SDK names), the
  launchers' `HOST` and `PORT`, and `VIRTUAL_ENV` and `RUNNER_TEMP`, which a
  venv and GitHub Actions set. Names that only other software reads (the
  `openai` SDK's `OPENAI_*`, proxy and certificate variables, and so on) are
  listed in `EXTERNAL_NAMES` and are never renamed.
- Three old pairs became one name each: `VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED`,
  `VOINEY_LAB_SUPPLEMENTAL_MODEL` and
  `VOINEY_LAB_EXTERNAL_REFERENCE_TIMEOUT_SECONDS`. The conflict check between
  two names of one setting now applies only to
  `VOINEY_LAB_EXTERNAL_REFERENCE_DOMAINS` and its alias
  `VOINEY_LAB_EXTERNAL_REFERENCE_ALLOWED_DOMAINS`.
- There is no transition period. Instead the server (after it loads the
  repository `.env`), the handoff worker, `scripts/run_dev.sh`,
  `scripts/run_pilot.sh`, `scripts/run_ci_server.sh`,
  `scripts/collect_chunks.sh` and pytest refuse to start while an old name is
  set, with "옛 설정 이름 N개: (names). scripts/migrate_env.py 를 실행하세요." The
  names are shown, never a value.

### Migrating a `.env`

```bash
python scripts/migrate_env.py --check [path]   # names only; writes nothing
python scripts/migrate_env.py --write [path]   # backs up, renames, regroups
```

The path defaults to the repository `.env`. `--write` copies the file to
`<name>.bak-YYYYMMDD-HHMMSS` beside it (mode 600), renames the old names, and
writes the settings back grouped by area (공급자 키, OCR, 모델, 기능 켜기·끄기,
경로·저장소, 그 밖), sorted, each under a short comment, with the file at mode
600. The text after every `=` is copied character for character. Names the
code does not read are kept, at the bottom under "코드가 읽지 않는 설정". It
stops before writing anything when a name appears twice -- including an old
and a new name for one setting, or both old names of a collapsed pair -- when a
line is not `.env` syntax.

Old names set anywhere else -- a shell profile, a deploy or helper script, a
service unit, a CI secret -- have to be renamed by hand; the refusal names
each one.
