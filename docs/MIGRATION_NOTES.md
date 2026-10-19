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

## Lane DI (2026-10-08): tables kept, writers removed

The MVP diet removed the reviewer and lab-admin screens, protocol approval
and revocation, lab adaptations, the source inbox, the protocols.io / Google
Drive / GitHub connectors and their webhook receipts, the dry-lab
(Snakemake/Nextflow) metadata, the eLabFTW write-back, analytics and the
admin audit. The commercial workspace schema stays at 7: no table is dropped,
no migration runs, and an existing database opens unchanged. Nothing writes
to those tables any more; their historical rows are kept as record. Reading
them is possible only with SQLite directly.

Also removed, with no storage of their own: the semantic-intent fallback, the
xAI web-image search and image generation, the config-gated legacy
`procedures.py` / `procedure_store.py` stack and its demo database under
`data/procedure_demo/` (deleted from the repository; a copy on a host is
simply unused), and the roles/permission table of the identity layer (every
principal is the one experimenter identity; the `roles` column keeps its
historical values).

Removed settings, written here without their `VOINEY_LAB_` prefix because
the setting-names test counts a prefixed name in a document as a current
one (`scripts/migrate_env.py --check` reports them under "코드가 읽지 않는
설정"): `PROCEDURE_CATALOG`, `PROCEDURE_STORE`, `SECRET_REFERENCES`,
`DEV_AUTH_PROFILES`, `OIDC_ROLES_CLAIM`, `GENERATED_VISUALS_ENABLED`,
`GENERATED_VISUAL_MODEL`, `GENERATED_VISUAL_TIMEOUT_SECONDS`,
`WEB_VISUAL_SEARCH_ENABLED`, `WEB_VISUAL_TIMEOUT_SECONDS`,
`SEMANTIC_INTENT_ENABLED`, `SEMANTIC_INTENT_MODEL`,
`SEMANTIC_INTENT_TIMEOUT_SECONDS`, `SEMANTIC_INTENT_MIN_CONFIDENCE` and
`SEMANTIC_INTENT_MUTATION_MIN_CONFIDENCE`.

## Lane CL (2026-10-10): code without storage of its own removed

No schema changes and no migration. Removed: the approved-safety-manual and
lab-reference searches (the voice answer from approved lab references, which
the research path ran on a related question), the safety report tools and
the report hand-off worker `python -m voiney_lab.worker` with its
`report.status.get` control, and the Moss reranker with its index sync script.
The worker's files on a host (`reports/inbox.jsonl`, `reports/processed.txt`,
`reports/status/`, `outbox/*.eml`) are no longer read or written; nothing
deletes them. Past experiment records that cite `approved_lab_corpus` keep
their rows and are still shown. `GET /readyz` no longer returns
`moss_enabled`.

Removed settings, without their `VOINEY_LAB_` prefix as above:
`LAB_MANAGER_EMAIL`, `FROM_EMAIL`, `MOSS_PROJECT_ID`, `MOSS_PROJECT_KEY`,
`MOSS_AUTO_REFRESH`, `MOSS_INDEX_NAME`, `MOSS_MODEL_ID`,
`MOSS_ALLOWED_SCOPES`, `MOSS_ALPHA`, `MOSS_CANDIDATE_LIMIT`,
`MOSS_QUERY_TIMEOUT_MS`, `MOSS_LOAD_TIMEOUT_SECONDS` and
`MOSS_REFRESH_SECONDS`. A `.env` that still sets one starts as before; the
name is simply not read (`scripts/migrate_env.py --check` lists it under
"코드가 읽지 않는 설정"). `VOINEY_LAB_MOSS_ENABLED` stays in the table only
because the launchers and the test baseline command still set it to false;
nothing reads it.

The setting table's default for each role's provider and model, and for the
STT and TTS providers, is now "설정 필요" (human decision 2). The code's old
xai/grok fallback was left in place by lane CL, so an environment that set no
role behaved as before; lane PL removed it from three files (next section).
`.env.example` sets every role to the values in use.

## Lane PL (2026-10-13), stage 1: an unset role no longer falls back to xAI

No schema change and no migration. Lane CL's table said "설정 필요" for every
role's provider and model, but `external_references.py`,
`experiment_reports.py` and `voice_providers.py` still answered xai and
`grok-4.6` for an unset one, so a role nobody set could reach a provider nobody
chose. They now say what is missing:

| Setting is unset | Before | Now |
|---|---|---|
| `EXTERNAL_REFERENCES_ENABLED=true` without `VOINEY_LAB_SUPPLEMENTAL_PROVIDER` / `_MODEL` | provider xai, model `grok-4.6` | the WebSocket refuses with "invalid non-secret configuration" naming the unset setting(s); the provider must be `xai` (the search is xAI's tool) |
| `VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED=true` ("AI 일반 지식") without the same two | model `grok-4.6` | the same refusal |
| `VOINEY_LAB_REPORT_PROVIDER` or the report model (`VOINEY_LAB_REPORT_MODEL`, else `VOINEY_LAB_SUPPLEMENTAL_MODEL`) | model `grok-4.6` on xai | the report's prose writer is off (one warning in the log naming both settings); the report keeps the server's own sentences |
| `VOINEY_LAB_STT_PROVIDER` / `VOINEY_LAB_TTS_PROVIDER` | xai | no provider: a turn's transcription or speech refuses by the setting's name, which the existing STT/TTS failure path handles; the `ready` event's `voice_profile` reports provider and voice `""` |

What a `.env` needs: nothing, if it was copied from `.env.example` (every role
is written there). A `.env` that relied on the fallback has to write the role.
`scripts/run_dev.sh` turns the general explanation on, so its "Non-secret
capability check" now stops, with the same named refusal, when the
supplemental role is not written down. `scripts/run_pilot.sh` keeps the
explanation off unless the shell or `.env` turns it on (and then the server
refuses at the first connection, as above).

Removed in the same lane: `retrieval.search_safety_documents` (nothing in
`src` called it; `retrieve_approved_lab_documents` and
`scripts/audit_approved_catalog.py` stay), `scripts/setup_moss_demo.py`, and
the `VOINEY_LAB_MOSS_ENABLED` export and the "moss_safety_documents" banner
line in `run_pilot.sh`, `run_dev.sh` and `run_ci_server.sh`.
`VOINEY_LAB_MOSS_ENABLED` is still in the setting table and in
`scripts/collect_chunks.sh`; nothing reads it.

Not changed by this lane (other lanes' files): the xai default and
`DEFAULT_MODELS` in `model_providers.py`, the old defaults in `multi_brain.py`
and `llm_router.py`, and the xAI-only web evidence search
(`XaiAuthoritativeWebSearch`, `EXTERNAL_REFERENCES_ENABLED`), which
`server.py`'s research path still calls. The table's comment on `_SET_ME` in
`setting_names.py` (and the README's provider section) still describe the
fallback as present for all roles. (Lanes AN and RA then removed the old
defaults, and lane DOC3, 2026-10-14, fixed those two descriptions and deleted
the web evidence search and the `MOSS_ENABLED` row: see "Lane DOC3" at the end.)

## Lane PL (2026-10-13), stage 2: the pilot path

No schema change and no migration; nothing already stored is read differently.

- **A new runtime usage scope, `source_protocol_only`** (human decision of
  2026-10-13, "B안"): `VOINEY_LAB_USAGE_SCOPE=source_protocol_only` needs no
  approved safety catalog and reads none (`server_config()` returns
  `catalog_path=None`; a configured catalog is ignored). Every scope that has a
  catalog is checked exactly as before. No catalog document carries the new
  value; `documents.usage_scope` is unchanged. A server, `.env` or script that
  does not use it sees no difference.
- **Additions, none of which an older reader needs** (a field it does not know
  is ignored):
  - `GET /api/protocols/{id}/review` has `safety_scope`:
    `{"usage_scope", "source_protocol_only", "notice"}` (`notice` is the
    sentence "기관 안전 문서 없음 · 원문 안전 주의만" under the new scope, else
    null).
  - `SafetyPack.public_dict()` (the `session.safety_pack` event) has
    `scope_notice` (the same sentence or null); under the new scope
    `coverage_status` is `"source_protocol_only"`, `missing_coverage` is
    `["institution_safety_documents"]` and no document is held.
  - A safety answer's `reply.complete` text ends with that sentence under the new
    scope (display only, never spoken).
  - The `safety_notices_acknowledged` ledger event's payload gains
    `safety_scope` and `institution_safety_notice` under the new scope; under any
    other scope the payload is what it was. Older events lack the two keys.
- **`GET /api/protocols`**: a development fixture that cannot be loaded is left
  out of the list, and one the catalog does not match is listed as not
  executable (`execution_blocked_reason: "development_fixture_conflicts_with_catalog"`);
  before, either failed the whole list with a 503. The uploaded protocols are
  listed in both cases. The single-entry, figure and page endpoints still load
  the fixture first and are unchanged.
- **`scripts/run_pilot.sh`**: no catalog file at
  `data/runtime/pilot/approved_safety_catalog.sqlite` now starts as
  `source_protocol_only` instead of refusing (exit 0 for `--check-only`). A file
  that is there keeps every check and is never taken for "no catalog". It also
  pins `VOINEY_LAB_STT_DIAGNOSTIC_DIR` inside `data/runtime/pilot/` and writes
  `.voiney-pilot-data` there when it really starts.
  **A host that relied on the refusal** (a pilot that must not start without
  institution documents) now has to put a check of its own in front, e.g.
  `--check-only` output must show `USAGE_SCOPE    = reference_only`.
- **New tools and documents**: `scripts/pilot_data_delete.py` (preview, confirm,
  delete, record; appends to `data/runtime/pilot/deletion_log.jsonl`),
  `scripts/pilot_https_setup.sh` with `scripts/pilot_https/Caddyfile.example`
  (written, not run), `docs/PILOT_GUIDE.md`, `docs/PILOT_HTTPS_SETUP.md`.
- `setting_names.py` lists the new scope on the `VOINEY_LAB_USAGE_SCOPE` row. No
  setting was added or removed.
- **`.env.example` rebuilt** (human instruction of 2026-10-13: it had not been
  brought up to date at all). It now lists every setting in the table except the
  ones whose reason is written in `tests/test_lane_pl_env_example.py` (shell/CI
  names, aliases, switches that follow another switch, the development
  launcher's fixture paths), 117 of the 136; its comments no longer describe
  the removed xai fallback or the Moss setting; and a copy of it as it stands is
  safe: the two keyed features (`VOINEY_LAB_WEB_EXPLANATIONS_ENABLED`,
  `VOINEY_LAB_DRAWN_DIAGRAMS_ENABLED`) start off (a copy used to be refused at
  the first connection without `OPENAI_API_KEY`), `VOINEY_LAB_ADMIN_TOKEN` is
  empty (it was a guessable placeholder, which opened `GET /api/admin/metrics`
  to anyone who knew it), `VOINEY_LAB_STT_DIAGNOSTIC_DIR` is empty (the
  placeholder lay outside `data/runtime`, which the server refuses), the usage
  scope is `demo` (the development fixture's scope; the comment says how to
  build the fictional demo catalog the way CI does, and that
  `source_protocol_only` needs no catalog), and `VOINEY_LAB_MOSS_ENABLED` is
  gone. The role providers, models and timings in use are kept. **A `.env`
  already copied from the old example is not touched**; compare it with the new
  one, and in particular check that it does not still hold the old admin-token
  placeholder.

Going back: code older than this stage rejects `source_protocol_only` as an
invalid usage scope (`server_config()`); set a scope with a catalog or stay on
this version. Nothing stored needs undoing; `deletion_log.jsonl` and
`.voiney-pilot-data` are plain files an older version ignores.

## Commercial workspace schema 7 → 8 (lane CF, 2026-10-08)

Lane DI left the schema at 7. The same day lane CF raised it to 8 (commit
`44aa23d`, decisions 1–2) to keep how each experimenter wants a recorded value
confirmed. `MIGRATION_7_TO_8` in `workspace_store.py` adds one table and
changes nothing else:

- `experimenter_settings`: one row per change -- `sequence_id`,
  `organization_id` and `principal_id` (foreign keys to `organizations` and
  `principals`), `name` (`confirm_mode` or `question_timing`, by a CHECK),
  `value`, `source` (`voice` or `screen`, by a CHECK) and `created_at`, with
  an index on (organization, principal, name, sequence). Update and delete
  triggers make it append-only. The values a name may take are checked in
  code (`EXPERIMENTER_SETTING_VALUES`: `readback`, `confirm`, `quiet`;
  `before_start`, `during`), not by the column.
- `schema_metadata` is rebuilt to hold 8, as each earlier migration did.

No existing table or row is changed. A schema-7 database is migrated in place
when the workspace is opened (`initialize_workspace_store`), in one
`BEGIN IMMEDIATE` transaction that is rolled back on failure ("Commercial
workspace migration failed."); a new database runs schema 1 and every
migration up to 8. `tests/test_lane_cf_confirm_mode.py` builds a schema-7
store, opens it, and checks the version and the append-only triggers.

Lane CF's decision 3 (`68662d0`) changed no schema: a confirmed revert is
appended as a `step_reverted` event to the existing workspace progress record
and to the experiment report, beside the completions it takes back, which
stay in the record.

### Reading older data

A store migrated from 7 holds no setting rows, so every experimenter starts
with the defaults (`EXPERIMENTER_SETTING_DEFAULTS` in `server.py`:
`confirm_mode=readback`, `question_timing=before_start`). A setting's value is
its latest row by `sequence_id`; the earlier rows stay as the record of the
changes. Without a workspace (`VOINEY_LAB_WORKSPACE_ENABLED=false`) the
settings are held in the server's memory and are gone when it stops. Lane
WV's `web_lookup` (2026-10-09) is not in this table: it is held in memory
beside it and starts `on` (until schema 9, below, puts it in the table).

### Going back

There is no down-migration. Code older than `44aa23d` expects schema 7 and
refuses a schema-8 database when it opens it ("Commercial workspace schema is
unsupported."). Going back means restoring the copy taken before the upgrade
-- the schema 3 → 4 procedure: stop, back up the SQLite file with its WAL/SHM
files, start one instance -- and that copy has none of the settings recorded
since. Moving forward again runs the migration again; its table, index and
triggers are created with `IF NOT EXISTS`. After an upgrade, confirm
`schema_metadata.schema_version = 8`.

## Lane BT (2026-10-09): the development fixture's event id

No schema change, and no stored row is rewritten or deleted. This is the
protocol store (`protocol_workspace.sqlite`), not the commercial workspace.

`ProtocolCatalog.bootstrap_development_fixture` records each load of the
curated development fixture as a `development_fixture_materialized` event.
Its id used to be `development-fixture-<fixture SHA-256, first 48>-<analysis
payload SHA-256, first 16>`; it now ends with `-<event payload SHA-256, first
16>` as well, the digest of the payload as the store writes it.

The reason: lane DI (`2cbdcff`, 2026-10-08) dropped `"final_approval": false`
from that payload. A store that had loaded the fixture before then held a row
under the same id with the old payload; the store refused the different
content (`DuplicateProtocolIdentifierError`), and from 2026-10-08
`scripts/run_dev.sh` stopped before it started the server.

### Reading older data

The older rows keep their ids, payloads (with `final_approval`) and times. The
first load with this version appends one new event, under the new id, for the
fixture's analysis; a later load of the same fixture appends nothing. A
store first loaded after lane DI, whose row already has the current payload
under the old id, also gets one new event with the same content. The readers
take any matching event of the type and never one in particular:
`development_fixture_is_materialized` looks for one whose payload is the
current one, and the catalog's `development_only` for any one of the type on
the revision.

`scripts/run_dev.sh` also changed (decision 3): when the fixture does not
load, it prints `[WARN] 개발 픽스처를 적재하지 못함 — 이 픽스처는 실행할 수
없음: <error type>` and still starts the server, which leaves that fixture
unrunnable. `--bootstrap-only` still ends with a non-zero code.

### Going back

Code from before lane DI computes the old id with the old payload, which
matches its own older rows. Code from lane DI up to this change computes the
old id with the new payload, so on a store that holds a pre-DI row it refuses
again, exactly as it did from 2026-10-08. The events this version appends need
no undoing.

## Commercial workspace schema 8 → 9 (lane VT, 2026-10-09)

Lane VT's decision 7 raised the schema to 9 so that two more settings are
kept in `experimenter_settings`: lane WV's `web_lookup` ("웹 찾아보기", `on`
or `off`), which was held only in the server's memory, and lane VT's
`proactive_mode` ("먼저 알려 주기", `all`, `needed` or `off`). The table's
`name` CHECK allowed only `confirm_mode` and `question_timing`, and SQLite
cannot widen a CHECK in place, so `MIGRATION_8_TO_9` in `workspace_store.py`
builds the table again:

- the rows are copied to `experimenter_settings_v8`, and the table's
  AUTOINCREMENT counter (its `sqlite_sequence` row) to
  `experimenter_settings_v8_sequence`;
- the table is dropped (a drop fires no trigger, so the append-only triggers
  do not refuse it) and created again, column for column as before, with
  `name IN ('confirm_mode','question_timing','web_lookup','proactive_mode')`;
- every row is copied back with its own `sequence_id`, and the counter is put
  back as it was, so the next row gets the number it would have got;
- the index `experimenter_settings_principal` and the triggers
  `experimenter_settings_no_update` and `experimenter_settings_no_delete` are
  created with the same statements as schema 8's, and both copies are
  dropped;
- `schema_metadata` is rebuilt to hold 9, as each earlier migration did.

No other table or row changes. The values each name may take are still
checked in code (`EXPERIMENTER_SETTING_VALUES`). The migration runs in one
`BEGIN IMMEDIATE` transaction when the workspace is opened and is rolled back
on failure ("Commercial workspace migration failed."); a new database runs
schema 1 and every migration up to 9. `tests/test_lane_vt_settings_store.py`
builds a schema-8 store with rows of two experimenters and a counter past its
last row, opens it, and checks the rows, the counter, the index and trigger
statements, the append-only triggers, the two new names, and that opening it
a second time changes nothing.

### Reading older data

Every setting a schema-8 store holds keeps its value: a value is still the
latest row of its name by `sequence_id`. A store migrated from 8 holds no
`web_lookup` or `proactive_mode` row, so they start at their defaults
(`EXPERIMENTER_SETTING_DEFAULTS` in `server.py`: `web_lookup=on`,
`proactive_mode=all`). A `web_lookup` changed before this version was held
in memory only and was gone when that server stopped; it is not recovered.
From this version a change of either setting, by voice or on the screen, is
appended to the table like the others. Without a workspace the settings are
still held in the server's memory.

### Going back

There is no down-migration. Code older than this version expects schema 8
and refuses a schema-9 database when it opens it ("Commercial workspace
schema is unsupported."). Going back means restoring the copy taken before
the upgrade -- the schema 3 → 4 procedure: stop, back up the SQLite file with
its WAL/SHM files, start one instance -- and that copy has none of the
settings recorded since. Moving forward again runs the migration again.
After an upgrade, confirm `schema_metadata.schema_version = 9`.

## Workspace schema 9 → 10: two more experimenter settings (2026-10-10, lane SP1)

Lane VT's schema 9 let the `experimenter_settings` table take four names by
a CHECK (`confirm_mode`, `question_timing`, `web_lookup`, `proactive_mode`).
Lane SP1's "주변 소리" (`ambient_mode`: `quiet` / `noisy`, decision 3) and
"목소리 민감도" (`speaker_sensitivity`: `high` / `normal` / `low`, decision 1)
are kept like the others, so the table takes six names.

### What changes

Exactly what schema 8 → 9 did, one name list wider:

- `experimenter_settings` is copied to `experimenter_settings_v9` (and its
  AUTOINCREMENT counter to `experimenter_settings_v9_sequence`), dropped, and
  created again with `name IN ('confirm_mode','question_timing','web_lookup',
  'proactive_mode','ambient_mode','speaker_sensitivity')`;
- every row is copied back with its `sequence_id`, and `sqlite_sequence` is
  put back to the counter as it was, so the next change gets the next id;
- the index `experimenter_settings_principal` and the two append-only
  triggers (`experimenter_settings_no_update`, `experimenter_settings_no_delete`)
  are created with the same statements as schema 9's, and both copies are
  dropped;
- `schema_metadata` is rebuilt to hold 10, as each earlier migration did.

No other table or row changes. The values each name may take are still
checked in code (`EXPERIMENTER_SETTING_VALUES`). The migration runs in one
`BEGIN IMMEDIATE` transaction when the workspace is opened and is rolled back
on failure ("Commercial workspace migration failed."); a new database runs
schema 1 and every migration up to 10. `tests/test_lane_sp1_settings_store.py`
builds a schema-9 store with rows of two experimenters and a counter past its
last row, opens it, and checks the rows, the counter, the index and trigger
statements, the append-only triggers, the two new names, and that opening it
a second time changes nothing.

### Reading older data

Every setting a schema-9 store holds keeps its value. A store migrated from 9
holds no `ambient_mode` or `speaker_sensitivity` row, so they start at their
defaults (`EXPERIMENTER_SETTING_DEFAULTS` in `server.py`: `ambient_mode=quiet`,
`speaker_sensitivity=normal`). The wearer's measured voice level itself
(decision 1) is never stored: it is measured again each session.

### Before the first start after the upgrade

Stop the server and copy the workspace SQLite file with its `-wal` and `-shm`
files (the schema 3 → 4 procedure), then start one instance and confirm
`schema_metadata.schema_version = 10`.

### Going back

There is no down-migration. Code older than this version expects schema 9
and refuses a schema-10 database when it opens it ("Commercial workspace
schema is unsupported."). Going back means restoring the copy taken before
the upgrade, and that copy has none of the settings recorded since. Moving
forward again runs the migration again.

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
- Three old pairs became one name each: `EXTERNAL_REFERENCES_ENABLED`,
  `VOINEY_LAB_SUPPLEMENTAL_MODEL` and
  `EXTERNAL_REFERENCE_TIMEOUT_SECONDS`. The conflict check between
  two names of one setting now applies only to
  `EXTERNAL_REFERENCE_DOMAINS` and its alias
  `EXTERNAL_REFERENCE_ALLOWED_DOMAINS`.
- There is no transition period. Instead the server (after it loads the
  repository `.env`), the handoff worker (deleted on 2026-10-10, lane CL),
  `scripts/run_dev.sh`,
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

## Lane EV2 (2026-10-10): the fields an analysis emptied

No schema change and no stored row rewritten. This is the protocol store
(`protocol_workspace.sqlite`), where an analysis is kept as the JSON of its
domain records.

`ExperimentProtocol` has one more field, `cleared_fields` (a tuple of names,
default empty). Since lane EV2's decision 2 an analysis whose only evidence
failures are in fields execution never reads -- the metadata fields named by
`CLEARABLE_METADATA_FIELDS` (title, authors, the three dates, version, DOI,
source URI, license, source status), the description and a section's title --
passes with those fields emptied (`""` for the title and a section title,
`()` for the authors, `None` for the rest, and the field's own evidence
`None`), and their names are recorded here: `metadata.<field>`,
`description`, `sections.<section_id>.title_source_text`. The
`protocol_analysis_ready` event carries the same list as `cleared_fields`
when it is not empty. `validate_protocol` accepts an empty title or section
title only when it is recorded so, and refuses a record that names any other
field or a field that is not empty. The provider is never asked for the field
and a response that sends it is refused.

### Reading older data

A payload written before this version has no `cleared_fields`; the decoder
fills the default (`()`), so it reads as an analysis that emptied nothing,
which it is. Nothing is migrated.

### Going back

Code from before this version refuses a payload that has `cleared_fields`
("Stored Protocol analysis record fields are malformed."): an analysis that
passed under this version cannot be read by older code. An analysis stored by
this version that emptied nothing still carries the field (as `[]`), so the
same holds for it. To go back, analyse the document again with the older code.

## Lane EV2 (2026-10-10): what a fixed repeat count means

No schema change and no stored row rewritten; protocol store again.

`FixedRangeRepetition` has one more field, `repeat_count_kind` (a string or
`None`, default `None`), and `repeat_count` now means the total number of runs
of the range, the first run included (lane EV2's decision 3). The analysis
response states the kind for every fixed repetition: `total`, `additional`
(the runs after the first: "once more", "Repeat steps 5 and 6") or
`ambiguous` ("Repeat steps 36-38 twice"). When it parses a fresh response the
server adds one to an `additional` count and stores no count for an
`ambiguous` one (the experimenter is asked for it before the start, or at the
range's first step with "실험 중에 묻기"). `validate_protocol` refuses any
other kind and a count beside `ambiguous`.

The response schema gained the field, so the curated development fixture's
pinned schema identity (`curated_protocol._CANONICAL_SCHEMA_SHA256` and
`canonical_schema_sha256` in
`data/fixtures/development_protocols/candidate_a_curated_analysis.provenance.json`)
was re-pinned to the new schema, as on 2026-10-05; the fixture itself is
unchanged and holds no fixed repetition.

### Reading older data

A payload written before this version has no `repeat_count_kind`; it reads
as `None`, its count is not changed, and the start screen shows one line for
it: "반복 횟수 확인 필요 · <range>단계 · 원문 “…” · 분석이 N회로 읽음(처음을
포함한 총 횟수인지 원문을 확인하세요)". Such a count is still led as stored. Re-run
the analysis to have it read with its kind.

### Going back

Code from before this version refuses a payload whose fixed repetition has
`repeat_count_kind` ("Stored Protocol analysis record fields are malformed.")
and refuses to load the curated development fixture under the re-pinned
provenance ("Development protocol fixture schema identity is unsupported.");
going back means restoring the older provenance file with the older code.

## Lane EV2 (2026-10-10): the provider may state a statement's second page

No schema change and no stored row rewritten. Since lane EV2's decision 5 the
analysis response schema asks for `SourceEvidence.continued_on_page_number`
and `continued_excerpt` (lane PA's fields, until now filled by the server
only), and the prompt asks a model to split a quote that runs onto the next
page into them. What a model writes there is kept only when the two pieces
are found joined across the page end, as when the server splits a joined
quote itself (decision 1); stored analyses already carry these fields, so
nothing about reading them changes.

The response schema changed again, so the curated development fixture's
pinned schema identity was re-pinned once more (the same three places as for
decision 3). Code from before lane EV2 refuses the re-pinned provenance, as
described above.

## Lane RN (2026-10-11): a passed analysis analysed again, beside the old one

No schema change and no stored row rewritten; protocol store (the catalog
ledger) again.

Since lane RN's decision 2 a person can ask for a new analysis of a protocol
revision that already holds a passed one ("다시 분석", its own route
`POST /api/protocols/{id}/reanalysis`). The ledger stays append-only: the
request is one more `protocol_analysis_requested` event whose payload now
also carries `reanalysis: true`, `previous_analysis_revision_number` and
`previous_analysis_payload_sha256`; a pass is one more row in
`analysis_revisions` (the next `analysis_revision_number`, so the entry's
`revision_id` goes from `pdf-1-analysis-1` to `pdf-1-analysis-2`); a failure
is one more `protocol_analysis_failed` event and nothing else. The old
analysis row, its payload and its events are never touched. The server
serves the latest stored analysis as the executable one; a failed
re-analysis therefore leaves the revision on the analysis it had.

The run status (`GET …/analysis/status`) gained `reanalysis`,
`available_for_execution` and `worker_running` (decision 1: whether this
server process still holds the run); the review gained `reanalysis` (the run
asked for over the pass, with its own `failure`) and, with decision 3,
`analysis_outdated_codes` / `analysis_outdated_ko` on every catalog entry
(`repeat_count_kind_missing`: a fixed repeat stored without
`repeat_count_kind`, that is, before lane EV2). None of these is stored.

An experiment already running holds the fixture it loaded at its start and
is not touched by a new analysis. Recovering a disconnected experiment,
however, requires the exact revision it started on
(`workspace_store.resume_experiment`), and the voice session loads the
latest executable fixture, so after a re-analysis has passed such a recovery
is refused with `WorkspaceConflictError` until the session start learns to
load the experiment's own analysis revision (see the lane RN report).

### Reading older data

Nothing changes: a request event without the new keys reads as a first
request, and a revision with one analysis reads as before.

### Going back

Code from before this version reads the ledger whole: the extra request
keys are ignored, and a second analysis revision is served as the latest, as
it already was for the development fixture (tests/test_aged_store.py). The
only difference is cosmetic: the older review puts a failed re-analysis's
code in `analysis_failure` beside `available_for_execution: true`, and the
older screen then shows "분석 실패와 복구" over a protocol that still runs.

## Lane AN (2026-10-13): the claim-chunk path deleted; unset model roles

No schema change and no stored row rewritten.

The evidence-first claim-chunk analysis path (lane AN, decision 1) is gone
with its two settings, written here without their `VOINEY_LAB_` prefix:
`PROTOCOL_CLAIM_CHUNKS_ENABLED` (default false; the only way into the path)
and `CHUNK_CACHE_DIR` (its development cache folder). A `.env` that still
sets one starts as before; the name is simply not read. Its modules
(`protocol_chunk_analysis`, `chunk_analysis_cache`, `claim_contract_audit`,
`protocol_claim_semantic_audit`, `protocol_claim_stream_telemetry`,
`protocol_provider_diagnostics`) live in the git history only;
`protocol_claim_analysis` keeps the page evidence segment generator, with
unchanged segment ids, for the in-gel fixture's timer manifest. The scripts
under `scripts/` that imported the deleted modules no longer run.

### Reading older data

A catalog ledger may hold `protocol_chunk_*` events from a run of that path;
the run status still reads them as before (none exist in the development
catalog of 2026-10-12). An analysis the path stored keeps its
`page_coverage` records in its payload, and the payload still reads, but the
catalog no longer derives the unread-page disclosure from them.

### Model roles without settings

`model_providers.RoleModel` no longer falls back to xAI and a grok model
when a role's settings are empty: an empty `VOINEY_LAB_<ROLE>_PROVIDER`
means the role is not configured ("설정 필요", as the setting table has said
since lane CL), `has_key()` is false and a client for it is refused with
`RoleNotConfiguredError` naming the setting. An environment that names every
role it uses, as `.env.example` does, behaves as before; one that relied on
the fallback must now set `VOINEY_LAB_<ROLE>_PROVIDER=xai` (and the model)
for each such role, or the role stays off.

## Lane AN (2026-10-13): a long document analysed in pieces

No schema change and no stored row rewritten; protocol store (the catalog
ledger) only.

A document of more than 60 pages, or one whose text is planned to need more
output than one analysis call gives, is analysed in pieces of at most 15
pages that share one page with the next (lane AN, decision 2). The run is the
analysis request's own: its events carry `analysis_run_id` equal to the
request's `analysis_id`. It writes, in this order, one
`protocol_chunk_plan_created` event (`chunks`: `chunk_id` `piece-<n>`,
`ordinal`, `source_page_start`, `source_page_end`), a
`protocol_chunk_analysis_started` event per piece (`attempt` 1, and 2 with
`retry_of_failure_code` when the piece is sent once more), a
`protocol_chunk_analysis_completed` (`steps`) or
`protocol_chunk_analysis_failed` (`failure_code`, `evidence_failure`) event
per piece, `protocol_chunk_merge_started` when every piece passed, and
`protocol_chunk_merge_conflict` (`reason`, `source_page_start`,
`source_page_end`) when two pieces read their shared page differently. The
run ends in the analysis' own `protocol_analysis_ready` event, which then
carries `pieces` (index, count, first and last page of each), or
`protocol_analysis_failed` with the new codes
`protocol_analysis_piece_failed` (and `failed_pieces`: each piece's pages and
last failure code) or `protocol_analysis_piece_conflict` (and
`piece_conflict`). A passed run is stored like any analysis; its ids carry
the piece they came from (`p2.step-14`). `protocol_analysis_too_many_pages`
now means more than 400 pages.

### Reading older data

Nothing changes for an analysis stored before: a ledger without piece events
reads as before. The run status reads chunk events only while their plan is
the latest analysis request's run, and a chunk failure that a later passed
analysis replaced is no longer shown as the current failure.

### Going back

Code from before this version reads a piece run's events as a claim-chunk run
(the same event types): while the run is going it shows its progress; after
it ended, its status may stay at the last chunk event. The stored analysis
itself reads and runs as any other.

## Lane AN (2026-10-13): evidence past a page's layout, and a person's retry retried

No schema change and no stored row rewritten.

The source-evidence check reads past five layouts the stored DS-2, M3 and EV3
answers showed (lane AN, EV4): a running header, footer or page number a page
prints in the middle of its body, a table header reprinted at the top of the
next page and a header table's number-only cells are left out of the
comparison; a soft hyphen, a subscript or superscript after a letter, and the
spaces between two Hangul letters on one line compare as the characters they
stand for (a superscript after a digit stays itself); a step label written
"3)" or "(2)" is stored as the bare number. Quotes are stored as the model
gave them.

A PDF whose text layer drops its Hangul glyphs (lines left holding only their
spaces and periods) now has each readable page marked for OCR with the new
`ocr_reason` value `dropped_glyphs`, beside `no_text_layer` and
`unreadable_glyphs`. The reason is computed at extraction and not stored by
itself; an OCR run's events record the pages read as before.

A person's "분석 다시 시도" after a failed analysis now gets the one automatic
retry a first analysis gets: `protocol_analysis_retry_started` may follow any
`protocol_analysis_requested`, at most once per request.

### Reading older data

A stored analysis is not checked again; one that passed before still reads.
A document registered before whose text layer drops its Hangul shows its
pages as needing OCR the next time it is read; its stored analysis, if any,
stays as it is. A ledger with two automatic retries on one revision (one per
request) reads as two runs, each with its own retry.

### Going back

Code from before this version reads all of the above: the new OCR reason is
an extraction value, not stored, and two retry events on a revision read as
the retry of the run on screen.

## Lane AN (2026-10-13): Word documents, photos and reagent tables

No schema change and no stored row rewritten; protocol store (the catalog
ledger) only.

`POST /api/protocols` also takes a Word document
(`application/vnd.openxmlformats-officedocument.wordprocessingml.document`)
and a JPG, PNG or HEIC photo (`image/jpeg`, `image/png`, `image/heic`,
`image/heif`). Each is turned into the PDF that extraction, OCR and the
analysis read -- a .docx by LibreOffice, a photo as one image page at 300 dpi
-- and that PDF is registered as the source, under the upload's name with
`.pdf` (`notes.jpg` -> `notes.pdf`). The `protocol_registered` event of such a
source carries `converted_from`: the upload's `media_type`, `sha256`,
`byte_size`, `filename` and `converter` (`pillow-12.3.0`,
`libreoffice-24.2.7.2`), and for a photo its `pixels`,
`exif_orientation_applied` and `pdf_dpi`. The same upload again is found by
that checksum.

`POST /api/protocols/{protocol_id}/reagent-tables` attaches an .xlsx workbook
to a protocol as its reagent table: one `protocol_reagent_table_attached`
event per workbook (`table_id`, `filename`, `media_type`, `sha256`,
`byte_size`, `authority` `person_upload`, `sheets` with each non-empty cell's
`ref`, `row`, `column`, `value`, `display` and, where present, `number_format`
and `formula`; `cell_count`; `warnings`). `GET
/api/protocols/{protocol_id}/reagent-tables` reads them back. A reagent table
is not a protocol source and changes nothing about the analysis or the
protocol's readiness.

The `protocol_ocr_completed` event's pages now carry `render_dpi` and
`value_boxes` (each a value's `text` and its `box` in the pixels of the page
rendered at `render_dpi`), so a value read from handwriting or a photo can be
shown beside the piece of the image it was read from.

### Reading older data

A registration without `converted_from`, an OCR page without `render_dpi` or
`value_boxes`, and a protocol without reagent-table events read as before (no
table, no boxes).

### Going back

Code from before this version reads every event above: it ignores
`converted_from`, `render_dpi` and `value_boxes`, and never reads the
reagent-table events. A protocol registered from a photo or a Word document
reads as the PDF it is.

### New dependencies

`Pillow` (photos) and `openpyxl` (reagent tables) are required; HEIC needs the
optional extra `.[heic]` (`pillow-heif`). LibreOffice must be installed on the
server, with the Korean fonts `fonts-nanum` (human decision 2026-10-13; a system
install, not a Python package): `VOINEY_LAB_OFFICE_CONVERTER` names its
`soffice` when it is not on `PATH` or in its default place, and without it a
Word upload is refused with `docx_conversion_unavailable`. `GET /readyz` adds
`docx_conversion_available` and `heic_photos_available` to its capabilities;
neither makes the server not ready. See `docs/SOURCE_FORMATS.md`.

## Lane RV (2026-10-14): analyses that wait for review, review decisions and review versions

No schema change and no stored row rewritten; protocol store
(`protocol_workspace.sqlite`) and the workspace's experiment rows.

Human decisions of 2026-10-12 (and the review scope of 2026-10-14) change
what an analysis whose quotes or claims are not all found in the source
becomes, and add a person's review that makes a version which runs. Three new
event types and one new kind of analysis revision record it, all append-only:

- `protocol_analysis_review_pending` -- an analysis kept for a person instead
  of failed: `analysis_id`, `flags` (each item that waits, by its id --
  `step:<step_id>`, `action:<action_id>/quantities/0`, `pages:41-59` for a
  long document's failed piece -- with the reason `evidence_not_found`,
  `ocr_unclear`, `ambiguous_repeat`, `not_in_source` or `piece_failed`, the
  refused field, the check's own reason code and the pages that print the
  quote when the cited one does not), `needs_review_count`,
  `verified_evidence_count`, `capability_policy_id`, `protocol` and
  `readiness` in the store's own tagged encoding, `source_pages` (the SHA-256
  of each page's text; the pages themselves are not copied and are read again
  from the stored source and its accepted OCR, and a page that no longer
  matches refuses the record) and, when present, `cleared_fields`, `pieces`,
  `response_repaired`. It is never an analysis revision: nothing that runs a
  protocol reads it.
- `protocol_review_decision_recorded` -- one decision on one item: `target`
  (the analysis, review-pending event or review version reviewed), `item_id`,
  `kind`, `action` (`confirm`, `fix`, `clear`, `enter`, `memo`, `reset`),
  `value` (`text`, `seconds` for a step timer, `count` for a repeat count),
  `source_lines` (page, first and last line and the excerpt they print, when a
  person pointed at where an item whose quote was not found stands), `memo`,
  `reviewer_name` when given, and `original` (the value before and its origin,
  `source` or `ai_suggestion`). A later decision on the same item is the draft;
  every decision stays.
- An analysis revision whose `analysis_id` starts `review-v<N>-` -- the
  protocol of review version N, stored and validated like any analysis (every
  quote is the page's own text: a quote that was not found is replaced by the
  lines a person pointed at). It is a person's version, not the analyser's: the
  catalog's latest analysis skips it.
- `protocol_review_version_registered` -- beside that revision
  (`analysis_revision_number` set): `version`, `revision_id`
  (`pdf-<n>-review-<N>`, the id a session and the workspace's experiment row
  bind to), `reviewer_name` (required), `base` and `base_revision_id` (what was
  reviewed), `changes` (each item changed or confirmed: original and reviewed
  value, origin `reviewer_input` or `ai_suggestion`, the words the run says for
  it, who decided it and when), `timers` (step id -> the reviewer's seconds, or
  `null` for no timer), `origins`, `memos`, `decision_event_ids`.

The latest registered review version is what a protocol runs from then on,
whatever the analyser makes after it; without one, the analysis that passed
runs as before (the review is optional). A recovered experiment loads the
revision it started on (`load_fixture_for_revision`), so a re-analysis or a
new version no longer refuses it.

### Reading older data

A store without these events reads as before: no analysis waits for review,
no protocol has a review version, and every protocol's revision id is
`pdf-<n>` or `pdf-<n>-analysis-<m>`. Checked on a copy of
`data/runtime/candidate-a-live-acceptance` (lane RV report, decision h): the
catalog listing, the review, the review tab's draft and the pipeline line of
all seven protocols read, `/api/protocols` lists them, a review version
registered on the copy runs, and the analysis it was reviewed from still loads
by its revision id.

### Going back

Code from before this version ignores the three event types. It reads a
review version's analysis revision as one more analysis (`pdf-<n>-analysis-<m>`
with the version's protocol), which it then runs; an analysis that waits for
review reads as an analysis still running (its start event has no end it
knows), and "분석 다시 시도" asks again. Checked on the same copy with the code
of `297d69d`.

## Lane RV2 (2026-10-16): review by item -- settled items, cards and their records

No schema change and no stored row rewritten; protocol store
(`protocol_workspace.sqlite`) only. Human decision of 2026-10-16: a person is
asked about items, one card at a time, and the server settles on
deterministic grounds what needs no person. New fields and one new event type,
all append-only:

- `protocol_ocr_completed` gains `engines` (the OCR engines the provider
  called: `clova`, `google`) and `pages_read_by_both` (the OCR pages both
  engines read -- neither failed on them). An OCR page both read with the same
  numbers and units (`numeric_review_required` false) settles the values
  executed items state there; any other OCR page makes each such value an item.
- `protocol_analysis_review_pending` and `protocol_analysis_ready` gain
  `auto_resolved`: what the server settled itself, each with its reason --
  `{"kind": "ocr_value", "page", "token", "value", "items", "reason":
  "ocr_two_engines_agree"}` and, for a long document, `{"kind": "same_step",
  "page", "kept", "joined", "same_words", "reason":
  "pieces_read_the_same_words"}`.
- A review-pending flag may carry `extra` and three new reasons:
  `ocr_value` (item id `ocr_value:<page>:<token>`, `extra` = the value as
  written, its token, the items that state it, the page's value-box indexes;
  `reason_code` `ocr_engines_differ` or `ocr_one_engine`), `read_two_ways`
  (on the kept step, `extra` = both pieces' readings and the other piece's
  steps in the store's tagged encoding) and `blocked_pages` (item id
  `pages:<first>-<last>`, `reason_code` `protocol_analysis_blocked`, `extra` =
  the provider).
- `protocol_review_decision_recorded` may hold the action `choose` with
  `value.reading` `a` or `b` (one of two pieces' readings), and
  `source_lines.suggested_by_server: true` when a person pressed [맞아요] on the
  line the server narrowed an item to.
- `protocol_analysis_auto_reanalysis` (new): an analysis with more items than
  `VOINEY_LAB_REVIEW_MAX_ITEMS` read once more, one reasoning level up --
  `analysis_id` (`<id>-more-1`), `reanalysis_of`, `reason`
  (`too_many_review_items`), `items`, `limit`, `by_reason`, `planned_calls`,
  `call_cap`, `reasoning_effort`, `previous_reasoning_effort` and `status`
  (`analyzing`, then `completed` with `items_after`/`by_reason_after` or
  `failed` with `failure_code`; `skipped_over_call_cap` when the calls it would
  take exceed `VOINEY_LAB_REVIEW_REANALYSIS_MAX_CALLS`).
- `protocol_analysis_failed` has two new failure codes:
  `protocol_analysis_too_many_review_items` (with `review_items`: `items`,
  `limit`, `items_after`) and `protocol_analysis_blocked` (with `blocked`:
  `provider`). A piece conflict may say `step_labelling_differs` with
  `unlabelled_pieces` and `labelled_pieces`.

### Reading older data

An OCR result stored before this version has no `engines`: its OCR pages read
as read by one engine, so the next analysis of such a document asks about the
values on them (an analysis already stored, passed or waiting, is read as
before). Flags without `extra`, decisions without `choose` and stores without
the new event read as before. Checked on a copy of
`data/runtime/candidate-a-live-acceptance` (sqlite through the backup API,
objects hard-linked; the original only read): the catalog listing, the review,
the review draft with its cards and the pipeline line of all seven protocols
read and `/api/protocols` lists them; on the copy, with fake models and fake
OCR, a document whose OCR engines differed was reviewed by its OCR value
cards (one value fixed, the sentence it stands in confirmed) and registered as
`pdf-1-review-1`, which runs; a document with 20 items ended with the
automatic re-analysis event and the guidance; a blocked document says "구글
안전 필터에 막혀 1–4쪽을 분석하지 못했어요." (lane RV2 report).

### Going back

Code from before this version ignores the new fields and the new event type.
A review version registered with the new decisions is an analysis revision as
before and runs; an analysis that ended with the guidance or a safety block
reads as a failed analysis with the default failure line ("분석 다시 시도"
asks again); an analysis waiting for review shows its items without the new
cards. Checked on the same copy with the code of `91b84c6`: all ten protocols
listed and reviewed, the registered version loads.

## Lane AN3 (2026-10-17): OCR numbers by place, one card per page, joined numbering, a block sent once more

No schema change and no stored row rewritten; protocol store
(`protocol_workspace.sqlite`) only. Human decisions of 2026-10-16 (A, B, C).
New fields and values, all append-only:

- `protocol_ocr_completed` pages gain `other_provider` and
  `other_provider_version` (the engine that also read the page), `other_text`
  (its whole text) and `value_readings`: each number the chosen engine read,
  `{"token", "text", "box": [x0, y0, x1, y1], "other_token", "other_text"}`,
  with the other engine's number in the same place on the page rendered at
  `render_dpi` (`other_*` null when it read none there). `value_readings` is
  null where the numbers were not compared place by place (one engine read
  the page, or an engine gave no word boxes).
- `auto_resolved` entries of kind `ocr_value` may carry `"by": "place"` (both
  engines read the value alike in every place it stands).
- `ocr_value` flags carry `extra.readings` (the chosen engine's reading, then
  the other engine's different ones: `{"engine", "text", "token"}`) and new
  `reason_code`s: `ocr_value_read_differently`, `ocr_value_one_engine`,
  `ocr_value_not_placed`. A page's values are one card, `ocr_page:<page>`;
  `protocol_review_decision_recorded` gains `page_card` on each confirm that
  card's [모두 맞아요] wrote (one event per value it confirmed).
- A new flag reason `numbering_seam` (item id `seam:<step id>+<step id>`,
  `reason_code` `step_labelling_differs`, `extra` = both steps' readings and
  pieces): where a long document's joined pieces change from numbered to
  unnumbered steps or back. Its decision is a `confirm` ([이어져요]).
- The domain rule "the steps are all labelled or all unlabelled" left
  `validate_protocol` for `validate_step_labelling`, which the analysis
  applies to a document read at once and to each piece. A joined analysis,
  its review-pending record and its review versions may hold both kinds.
- A `protocol_analysis_failed` with a piece conflict `step_labelling_differs`
  is no longer written (it still reads back as before).
- The provider resends a blocked reply once (lane AN3, C): a piece event's
  `provider_retries` may hold `{"reason": "blocked", ...}` rows.

### Reading older data

An OCR result stored before this version has no `value_readings`: its pages
are compared as lane RV2 compared them, by page (same numbers and units on
the page settle its values; any other page asks them). Flags, decisions and
cards stored before read as before; an analysis waiting for review shows its
OCR values on one card per page.
Checked on a copy of `data/runtime/candidate-a-live-acceptance` (sqlite
through the backup API, objects hard-linked; the original only read): the
catalog listing, the review, the review draft with its cards and the pipeline
line of all seven protocols read, and `/api/protocols` lists them; on the
copy, with fake OCR engines and fake models, a scanned page the two engines
read differently became one page card (`ocr_page:2`, the CLOVA reading chosen
for one value, the sentence it stands in confirmed) and was registered as
`pdf-1-review-1`, which runs; a document whose middle part prints no step
numbers became two seam cards, [이어져요] at both, registered, and runs with
the steps numbered 1-6 (lane AN3 report).

### Going back
Code from before this version ignores the new OCR fields and decision
markers: a registered version reviewed through a page card reads and runs,
and an analysis waiting for review shows its OCR values one item each. One
record it cannot read: a **registered** review version whose steps mix
numbered and unnumbered ones (decision B) -- the older `validate_protocol`
refuses it when the store loads it, and the older catalog's protocol list
then fails as a whole. A joined analysis still waiting for review reads (its
items as before). Checked on the same copies with the code of `81ad9ed`:
every other protocol listed, reviewed and loaded; the registered mixed
version raised `ProtocolSerializationError`. Before going back with such a
version stored, keep this version's `validate_protocol` (the rule moved to
`validate_step_labelling`) or restore a store backup taken before it.

### Round 2 (2026-10-18): poorly read pages, safety numbers, long documents

Human decisions of 2026-10-18. Append-only again; no schema change:

- `protocol_ocr_completed` pages gain `other_confidence` (the other engine's
  page confidence). A page whose numbers the engines read differently or
  without a partner at least 15% of the time, or with the lower confidence
  below 0.90, settles no value; such values are flagged with `reason_code`
  `ocr_page_differs_widely` or `ocr_page_low_confidence`.
- An `ocr_value` flag stated by a safety statement carries
  `extra.in_safety_statement: true`; its `fix` decision must be the other
  engine's reading. A review version registered with such a fix holds the
  statement with the reading copied in the value's place, and its `changes`
  gain an entry for the statement (`kind` `warning`, `action` `fix`,
  `origin` `source`).
- A document read in pieces writes no `protocol_analysis_auto_reanalysis`;
  its `protocol_analysis_too_many_review_items` failure names a `limit` of
  `VOINEY_LAB_REVIEW_MAX_ITEMS` plus its number of pieces.
- A new flag reason `reference_moved` (`item_id` `construct:<id>`,
  `field` `section_id`, `reason_code` `construct_step_in_other_section`,
  `extra` = `step_id`, `named_section`, `step_section`): the join pointed a
  construct at the section its step stands in.

Reading older data: an OCR result without `other_confidence` gates on the
chosen engine's confidence alone. Checked on a copy of
`data/runtime/candidate-a-live-acceptance` (sqlite through the backup API,
objects hard-linked; the original only read): all seven protocols read; on
the copy, with fake engines and models, a safety number the engines read
differently was registered with the other engine's reading copied into the
statement, and a joined document whose construct the join re-pointed was
confirmed and registered -- both run. Going back to the code of `f8e1a5e`:
every protocol, the two new versions included, lists, reviews and loads (the
new fields are ignored; a `reference_moved` flag reads as an item like any
other).

## Lane AN4 (2026-10-19): refused items asked about once more

No schema change and no stored row rewritten; protocol store
(`protocol_workspace.sqlite`) only. Human decision of 2026-10-19 (1). New
records, all append-only:

- A new event `protocol_analysis_evidence_requote`, written once per
  analysis run whose first answer had items the evidence check refused
  (원문에 없음, 근거를 못 찾음, OCR 흐림 with a quote, claim or step-number
  refusal): `{"status": "completed", "analysis_id", "questions", "items",
  "items_requoted", "by_outcome": {<outcome>: n}, "calls": [{"first_page",
  "last_page", "pages", "questions", "status": "answered" | "failed",
  "failure_code"?, "seconds"}], "check_seconds"}`. Outcomes: `requoted`,
  `model_found_nothing`, `no_answer`, `invalid_answer`, `value_differs`,
  `other_passage`, `still_refused`, `call_failed`. A long document has one
  call per piece with refused items. No source text is recorded.
- `auto_resolved` (on `protocol_analysis_ready` and
  `protocol_analysis_review_pending`) may hold entries
  `{"reason": "evidence_requoted", "reason_ko": "근거를 다시 찾아 확인",
  "item", "field", "was": <the flag reason it had>, "page"}` beside the OCR
  and same-step entries.
- The stored analysis (or review-pending protocol) holds the page's own words
  where an item was kept: its quote replaced by the stretch the re-ask
  returned (a step's or statement's words too when they were the refused
  quote's own), or a claim's words replaced by the page's. Values and units
  are the same as the analysis wrote (otherwise the item stays an item).
- Setting `VOINEY_LAB_ANALYSIS_REQUOTE` (default `true`; `false` turns it off).

### Reading older data

Nothing older is read differently: an analysis stored before this version
has no requote event and no such `auto_resolved` entries, and its items are
cards as before. The re-ask happens only right after a new analysis.

### Going back

Code from before this version ignores the new event and the extra
`auto_resolved` entries (it never reads `auto_resolved` back); an analysis
whose items were kept by the re-ask reads, lists and runs like any other,
since its quotes are the source's own words and pass the older checks.

## Workspace schema 10 → 11: the agent's voice and the calibration outcome (2026-10-18, lane ON)

Schema 11 is schema 10 with two more names in the `experimenter_settings`
CHECK: `agent_voice` (the voice the agent speaks with: the TTS provider's
voice id, or `default`) and `voice_calibration` (what the last "목소리
맞추기" came to: `none` / `measured` / `skipped`; the start screen skips the
step once measured). Human decision ② of 2026-10-16/18. Built the way
schema 10 was: the rows are copied with their sequence ids, the
AUTOINCREMENT counter is put back, the index and both append-only triggers
are made again (`MIGRATION_10_TO_11`). Nothing else in the store changes.

`EXPERIMENTER_SETTING_VALUES["agent_voice"]` is `None`: the store accepts an
identifier of up to 80 characters and the server checks it against the
configured provider's voices (`voice_providers.korean_voices`), since the
store does not know the provider. The server's own `synthesize` reads the
chosen voice from the speaking session (`ListenerSession.tts_voice`, set at
the connection from the setting) and the `ready` event's `voice_profile`
carries `voice_id` (the voice in use) and `agent_voice` (the setting).

New in the protocol store (`protocol_workspace.sqlite`), append-only, no
schema change: the event `protocol_start_check_answered` (lane ON, decision
③) -- one per answered question of the start check: `question_id`, `kind`,
`item_id`, `step_label`, `value` (the analysed value), `chosen`, `correct`,
`action` (`answer` / `accept`), `analysis_id`, `target`,
`actor_principal_id`, `recorded_at`. The catalog's readers ignore it.

### Reading older data

A schema-10 store migrates on open; it holds no row of the new names, so
the voice is the provider's default and the calibration step is shown until
it is measured once. Checked on a copy of
`data/runtime/candidate-a-live-acceptance` (sqlite through the backup API,
objects hard-linked; the original only read): the workspace opened at 11
with its rows, the voices listed, a voice saved and read back, the start
check of the in-gel protocol answered and recorded (lane ON report).

### Going back

Code from before this version refuses a schema-11 store ("Commercial
workspace schema is unsupported"): restore a backup taken before the
migration, or rebuild the table with schema 10's CHECK after deleting the
rows of the two new names. The `protocol_start_check_answered` events are
read by nothing older and do no harm.

## Lane ON (2026-10-18), stage ①: accounts beside the workspace

No workspace schema change (schema 10 stays) and no stored row rewritten.
Human decision ① of 2026-10-16/18: `VOINEY_LAB_AUTH_MODE` is `dev` (the
default: the one development identity, exactly as before) or `accounts`.

New, only in `accounts` mode:

- A second SQLite file beside the workspace's, `accounts.sqlite` in
  `VOINEY_LAB_WORKSPACE_DATA_DIR` (`voiney_lab.accounts`, its own
  `schema_metadata` at version 1): `accounts` (name, `name_key` for the
  one-per-name rule, the scrypt `password_hash`, lab, admin flag,
  `entitlement` = `pilot`, disabled flag), `invites` (the token's SHA-256,
  lab, admin flag, expiry, used/revoked marks, note), `sessions` (the
  token's SHA-256, expiry, revocation), `login_attempts` (name key, client,
  time; kept for the 15-minute window). No password and no token is stored
  in clear.
- An account is the principal `acct-<account id>` with
  `authentication_method="accounts"`, so the workspace store writes it into
  `principals`, `memberships`, `resource_bindings`, `experiment_sessions`
  (owner), `experiment_session_events` (actor), `experimenter_settings`
  and `admin_audit_events` as it wrote the development identity. The
  report ledger's `experimenter_recorded` event carries the account's name;
  the catalog's review decisions and versions carry it as
  `reviewer_name` (the server fills it in, whatever the screen sent).
- `scripts/pilot_data_delete.py --account NAME` deletes one account's rows
  from the three stores (lifting and restoring the append-only delete
  triggers inside one transaction) and its evidence files; the deletion log
  gains `"scope": "account"` (`"folder"` for the whole-folder path) and
  holds no name. A principal row that still owns a shared protocol
  (`resource_bindings` of type `protocol_catalog`) is kept with
  `display_name='(삭제된 계정)'` and a hashed `subject`.

### Reading older data

A workspace made before this version opens unchanged; in `accounts` mode
the account store is created empty beside it on first use, and an invite
made with `--lab <that workspace's organization id>` joins the existing
lab, so its protocol list is shared with the new accounts. Checked on a
copy of `data/runtime/candidate-a-live-acceptance` (sqlite through the
backup API, objects hard-linked; the original only read): workspace schema
10 opened, two accounts joined the lab `tenant-local-demo` through two
invites, both listed the seven protocols and read the in-gel review, the
administrator listed the lab's 26 experiments and the member none of their
own, logout closed the API (`~/reports/on/ledger/boot_check_stage1.json`).

### Going back

Code from before this version ignores `accounts.sqlite` and the
`VOINEY_LAB_AUTH_MODE` setting (the setting-name guard does not know the
name, so a `.env` that sets it is simply not read -- the older server then
runs without a login). Everything an account wrote is ordinary workspace
data under a principal id the older code treats like any other; the
development identity's rows are untouched. A principal row blanked by the
account deletion reads as a member without a name.

## Lane DOC3 (2026-10-14): the xAI web reference search deleted

No schema change and no migration; nothing already stored is read differently.
The web reference search was off by default and no launcher turned it on, so a
server that never switched it on behaves exactly as before. Human decision of
2026-10-14 (lane PL's report, section 7, listed the footprint).

Deleted:

| What | Where |
|---|---|
| `XaiAuthoritativeWebSearch` (the Responses `web_search` adapter, its cache, citation checks and answer clean-up), `ExternalReferenceSettings`, and the helpers only they used | `external_references.py` |
| The research path's second step, `search_authoritative_web` (its `tool.call` / `tool.result` events and the "authoritative_web" `research.state` phase), the "pending" announcement that went with it, and `ListenerSession.external_reference_settings` | `server.py` |
| Thirteen setting-table rows: the switch `EXTERNAL_REFERENCES_ENABLED`, the six time and turn limits (`EXTERNAL_REFERENCE_TIMEOUT_SECONDS`, `_CONNECT_`, `_READ_`, `_ENRICHMENT_BUDGET_`, `_SERVICE_TIER`, `_MAX_TURNS`), and `EXTERNAL_REFERENCE_DOMAIN_PROFILE`, `_DOMAINS`, `_ALLOWED_DOMAINS`, `_MAX_CITATIONS`, `_CACHE_TTL_SECONDS`, `EXTERNAL_SEARCH_DISPLAY_MODE` (all with the `VOINEY_LAB_` prefix) | `setting_names.py`, `.env.example` |
| The `VOINEY_LAB_MOSS_ENABLED` row (nothing has read it since lane CL) and the one `export` left in `scripts/collect_chunks.sh` | `setting_names.py`, `scripts/collect_chunks.sh` |
| The old-name entries whose new name is a deleted row (4 prefixed, 12 unprefixed) | `setting_renames.py` |
| The diagnostic script for the search | `scripts/diagnose_candidate_a_research.py` |
| The launchers' references: `run_dev.sh`'s eight defaults, its capability printout and import; `run_pilot.sh`'s default and banner line `external_references:`; `run_ci_server.sh`'s export | `scripts/` |
| The one entry of `XAI_ONLY_FEATURES` (the guard stays, empty, for the next xAI-only feature) | `configuration.py` |

Kept: the "AI 일반 지식" explanation (`XaiSupplementalKnowledge.explain` and
`explain_outside_pdf`, `SupplementalKnowledgeSettings`, the
supplemental role's settings), the query planners and the
`apply_reference_answer` method of `curated_protocol.py` (no longer called;
that file was outside this lane).

What changes for someone:

- `research_capabilities.external_text` in `session.ready` is now always
  `{"status": "disabled"}`. The screen still requires the entry; it used to
  carry the profile and the domain count.
- A `.env` that still sets one of the thirteen names is read by nothing. The
  start-up guard does not refuse them (they were never old names); delete the
  lines. `scripts/migrate_env.py` lists them under "코드가 읽지 않는 이름".
- `VOINEY_LAB_SUPPLEMENTAL_REASONING` stays (the supplemental role reads its
  reasoning setting through `model_providers`); its table default is now
  "(공급자 기본)" and its meaning "PDF 밖 설명 추론", where it said "low" and
  "외부 근거 검색 추론".
- `docs/APPROVED_DOCUMENT_OPERATIONS.md` §6 no longer describes the search.
  Documents that have to spell a deleted setting write it without the
  `VOINEY_LAB_` prefix (as earlier lanes did); the standard test command's
  `VOINEY_LAB_MOSS_ENABLED` (in `CLAUDE.md`, README and `docs/GIT_WORKFLOW.md`)
  is the one prefixed name documents may still spell
  (`tests/test_setting_names.py`).

Going back: code from before this version reads the same data; the deleted
settings were read only by the deleted code.
