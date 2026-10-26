# Migration Notes

## Workspace schema 11 → 12: translation style versions (2026-10-26, lane KO2)

Schema 12 adds `style_version` (an integer, `CHECK(style_version>=0)`) to
`protocol_fact_translations` and `protocol_translation_glossaries` and makes it
part of their identity: `UNIQUE(revision_id,fact_key,language,source_sha256,
status,style_version)` and `UNIQUE(revision_id,language,style_version)`. It is
the version of the translation instructions a row was made with
(`protocol_translation.STYLE_VERSION`, now 4; human decision 1 of 2026-10-26),
so the Korean of a protocol can be made again under new instructions while
the old rows stay. SQLite cannot change a UNIQUE constraint in place, so
`MIGRATION_11_TO_12` builds both tables again: every row is copied in its
order with its id and `style_version` 0 ("before versions were kept"), and
the index and both pairs of append-only triggers are made as schema 7 made
them. DROP TABLE fires no trigger. Nothing else in the store changes.

A row of version 0 keeps the id it had; a row of a later version has
`:v<N>` at the end of the identity its id is hashed from, so a new version's
row never collides with an older one. The same holds for a glossary's id.

### Reading older data

A schema-11 store migrates on open and reads as before: its rows are version
0 and are shown when they pass today's check, as they were. Reading now takes,
sentence by sentence, the newest version's row that passes today's check
(each row checked with its own version's glossary), else an older one that
passes, else the source; a reviewed row still wins. Generation makes only
what the current version lacks -- a sentence stored under an older version,
passed or refused, is made again; one stored under the current version is
not asked again. Opening a session does not regenerate an existing
protocol's rows (only a protocol with no row at all starts its generation,
as before): `scripts/retranslate_protocols.py --data-dir DIR` plans with no
call, and with `--run` makes the current version for the stored protocols
(keys from the shell; `.env` is not read). Checked on a copy of
`data/runtime/candidate-a-live-acceptance` (sqlite through the backup API,
objects hard-linked; the original only read): the store opened at 12 with
its rows, the plan read four runnable protocols, 223 sentences and 34 calls.

### Going back

Code from before this version refuses a schema-12 store ("Commercial
workspace schema is unsupported"): restore a backup taken before the
migration. Rows of a later style version would otherwise collide with the old
UNIQUE constraint; to rebuild schema 11 by hand, drop the rows whose
`style_version` is not 0, rebuild both tables without the column, and set
`schema_metadata` to 11.

## Page range and today's values (lane EASY, 2026-10-24)

No schema number changes. Three stores gain event types and payload keys;
nothing existing is rewritten.

- **Protocol catalog** (`protocol_workspace.sqlite`, events): an analysis
  request, its start and its result may carry `page_range`
  (`first_page`, `last_page`, `page_count`, `pages_ko`). New event types
  `protocol_run_changes_chosen` (the person's values for the next start:
  `change_set_id`, `actor_principal_id`, `target`, `revision_id`, `source`,
  `changes`, `recorded_at`) and `protocol_run_changes_applied`
  (`change_set_id`, `experiment_session_id`).
- **Experiment record** (`experiment_reports.sqlite`): `session_started` may
  carry `run_changes` and `run_change_set_id`; new event types
  `run_value_changed` and `run_value_reverted` (their `step_record` holds the
  change, what it replaced or what was put back, and the analysis or review
  version).
- **Workspace** (`commercial_workspace.sqlite`, `experiment_session_events`):
  `protocol_started` may carry `run_changes`; new event types
  `run_value_changed` and `run_value_reverted`. The append-only triggers are
  unchanged.

Follow-up of the same day: `protocol_run_changes_chosen` may also carry
`confirmed_large` (the item ids confirmed once more), and `session.start`
may carry `run_change_set_id` (`run-changes-<24 hex>`). With the start check
on, a client that sends no such id cannot start a new run -- an older page
must be reloaded after the upgrade. Rolling back: older servers (before
this lane) drop the unknown control field and apply no changed value.

Reading old records: an analysis without `page_range` read the whole
document, and a record or experiment without these events used no changed
value -- the notebook, the recovery and "지난번처럼" read them that way.

Rolling back to older code: older code ignores the new catalog event types
and `page_range` (an analysis made on a range still runs; its review shows no
range, and the possibly-missed notice it stored is the range's). Older code
ignores the new report and workspace events: a run continued on older code
uses the source's values, and its notebook no longer shows "오늘 바꾼 값".
No backup or migration step is needed; the events stay in the ledgers.

## Experiment record schema 1 → 2

The report store adds `organization_id`, `owner_principal_id` and
`ownership_migrated`. Startup and the report access boundary fill old records
from the matching workspace experiment session. If its owner cannot be found,
the report/protocol binding identifies only the lab: only that lab's
administrator may read it. A record with no identifiable lab stays inaccessible.
No historical event or prose is rewritten. The migration is idempotent;
new records receive ownership from the server principal, never request fields.
Back up the report and workspace databases together before upgrading. Older
servers refuse schema 2; restore the pair of backups to roll back.

HTTP exports, report review/status and socket report reads allow the owner or
the same lab's administrator. Corrections, value confirmation, ratings and
prose regeneration require the owner. The administrator may append only PI
confirmation to another person's notebook. Unavailable records return 404
(socket: `report_not_found`), including records from another lab.

Protocol registration now scopes checksum deduplication to the server's lab.
Existing bound protocol ids stay valid. A newly registered lab/source pair uses
a deterministic scoped id and its own registration event; the original PDF
checksum and content-addressed bytes stay identical. No catalog schema changes.
The starter pack uses the same registration scope.

Account/password confirmation lockout is now per account name, not per shared
network address. Password changes reuse the login failure budget. The invite
tool follows the pilot launcher's fixed workspace unless `--data-dir` is
explicitly supplied; shell workspace settings no longer redirect its writes.

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
the retired collector; nothing reads it. The collector was later deleted.

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
  (written, not run), `docs/PILOT_GUIDE.md`, `docs/archive/PILOT_HTTPS_SETUP.md`.
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
  pytest and the then-existing collector refuse to start while an old name is
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
  `other_passage`, `words_out_of_order`, `still_refused`, `call_failed`. A long document has one
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

Decision 3 (the same day): a Word document without Hangul uploaded from this
version is printed by LibreOffice with Calibri, Calibri Light, Aptos, Segoe
UI and Tahoma pointed at Carlito (when installed) or Liberation Sans, Cambria
at Caladea or Liberation Serif, Consolas at Liberation Mono (a per-run
fontconfig, as lane AN's Korean one). Its PDF -- the registered source -- may
break lines and pages differently from a PDF the older code printed for the
same file; nothing stored is rewritten, and a document already registered
keeps its PDF (the same .docx uploaded again is found by its own checksum, as
before). Korean documents are printed as before.

### Reading older data

Nothing older is read differently: an analysis stored before this version
has no requote event and no such `auto_resolved` entries, and its items are
cards as before. The re-ask happens only right after a new analysis.
Checked on a copy of `data/runtime/candidate-a-live-acceptance` (sqlite
through the backup API, objects hard-linked; the original only read): all
seven protocols list, review and -- the three that run -- load
(`pdf-1-analysis-10` for the in-gel fixture).

### Going back

Code from before this version ignores the new event and the extra
`auto_resolved` entries (it never reads `auto_resolved` back); an analysis
whose items were kept by the re-ask reads, lists and runs like any other,
since its quotes are the source's own words and pass the older checks.
Checked with the code of `515421f` on a copy of this lane's live catalog
(ten documents, two items kept by the re-ask in one review-pending analysis):
every protocol listed and reviewed with the same cards as the new code.

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

## Lane LOG (2026-10-18): pilot metrics beside the workspace

No workspace schema change and no stored row rewritten. Recorded here by lane
FX4 (decision 6, 2026-10-19); lane LOG added the file.

New: a SQLite file `pilot_metrics.sqlite` in `VOINEY_LAB_WORKSPACE_DATA_DIR`
(`voiney_lab.pilot_metrics`, `PILOT_METRICS_FILENAME`;
`data/runtime/pilot/workspace` for the pilot). It is created on the first
write and only while the workspace is on; without the workspace nothing is
stored (the `pilot.turn` server log line is still written). It holds
numbers, codes and times only -- no words said, no audio, no protocol text,
no model output -- in four tables, each row naming its account
(`account_id`: the request's principal id -- `acct-<account id>` for a
signed-in account, the development identity's otherwise -- or
`dev-local-admin` when no principal is known), with `recorded_at` in UTC:

| Table | One row per | Columns besides `id`, `recorded_at`, `account_id` |
|---|---|---|
| `pilot_turns` | voice turn (or tapped candidate) | `session_id`, `report_id`, `turn_id`, `router` (`off`/`front`/`used`/`fallback`/`stopped`), `reason` (`none`/`timeout`/`rate_limit`/`format`/`refused`/`error`), `action`, `source` (`voice`/`candidate`), `state_changed`, `step_advanced`, `clarified` (0/1), `first_audio_ms`, `total_ms` |
| `pilot_marks` | experiment-level mark | `session_id`, `report_id`, `kind` (`button_change`, `experiment_opened`, `connection_lost`, `timer_alert`, `alert_answered`, `report_exported`), `code`, `value_ms` |
| `pilot_protocol_events` | analysis outcome or registered review version | `protocol_id`, `revision_id`, `kind` (`analysis`/`review_registered`), `outcome` (`passed`/`review_pending`/`notice`/`failed`), `duration_ms`, `card_count` |
| `pilot_feedback` | end-of-experiment answer ("이번 실험에 도움이 됐나요?") | `report_id`, `rating` (1-5, or NULL when `skipped`=1), `skipped`, `note` (the optional one-line memo, at most 200 characters -- the only free text) |

The tables are made with `CREATE TABLE IF NOT EXISTS` on each write
connection and carry no schema-version row; there are no delete triggers.
Readers (`GET /api/admin/pilot-metrics`, `scripts/pilot_metrics.py`) open a
temporary copy or open the file read-only. A write that fails is logged as
`pilot.metrics.failed` and dropped.

Deleting pilot data: the whole-folder path of `scripts/pilot_data_delete.py`
deletes the file with the rest of the workspace folder. Until lane FX5
(2026-10-20) the `--account NAME` path did not read or change
`pilot_metrics.sqlite` (that account's metric rows stayed until the folder
was deleted); since lane FX5 it lists, confirms and deletes them (see "Lane
FX5" below).

### Reading older data

A workspace made before lane LOG has no `pilot_metrics.sqlite`; every reader
reads a missing file (or a missing table) as no rows, so the metrics of the
time before it are empty, not wrong. Nothing is filled in after the fact.

### Going back

Code from before lane LOG does not open the file; it stays in the folder,
unread, and does no harm. Delete it by hand if it should go.

## Lane FX5 (2026-10-20): an account's metrics deleted with it, an account that deletes itself, no credential kept

No schema change (workspace schema 11, accounts schema 1 stay) and no stored
row rewritten by the upgrade itself.

- **Deleting one account.** `scripts/pilot_data_delete.py --account NAME`
  now also lists, binds into its confirmation word and deletes that
  account's rows in `pilot_metrics.sqlite` (`pilot_turns`, `pilot_marks`,
  `pilot_protocol_events`, `pilot_feedback`, the rating's memo included; the
  rows are found by `account_id` = the account's principal id). It also
  rewrites, to `(삭제된 계정)`, the account's name where a record the lab
  shares carries it: `reviewer_name` and `decided_by` of review decisions
  and review versions (the catalog's `protocol_events` payloads, the
  append-only update trigger lifted and put back in one transaction) and
  the `name` of a PI confirmation on a review version (catalog) or on
  someone else's notebook (`experiment_report_events`, `pi_confirmed`).
  Only those name fields are rewritten; words a person typed are not. The
  rewritten payloads are compact canonical JSON (sorted keys, no spaces).
  Every deleting connection sets `PRAGMA secure_delete=ON`. An identity row
  (`principals`) that something still points at -- a shared protocol, or the
  lab's `organization_settings.updated_by_principal_id` -- is blanked, not
  deleted (`display_name='(삭제된 계정)'`, hashed `subject`).
- **One scope, two callers.** The inventory and the deletion moved from the
  tool into `voiney_lab.accounts` (`AccountDataLocations`,
  `account_data_inventory`, `delete_account_data`, `erase_account_data`,
  `write_deletion_log`); the tool keeps the preview text, the confirmation
  word, the open-file check and its log, and calls them. A deletion record
  (`deletion_log.jsonl`, beside the pilot folder's other records) gains
  `"via"`: `"tool"` or `"app"`; older lines have none. A record's overall
  `result` is `deleted`, `partial` or (new) `failed` when no store deleted.
- **`POST /api/auth/delete-account`** `{"password"}`: accounts mode,
  signed in. `AccountStore.switch_off_for_deletion` checks the password as a
  login is checked (the failure counts toward the lock), refuses the lab's
  last active administrator (`409 last_admin` with `message_ko`) and switches
  the account off with its sessions in one `BEGIN IMMEDIATE`; the server then
  calls `erase_account_data` and appends the record. A failure leaves the
  account switched off (`500 account_delete_incomplete`); the tool finishes it.
- **Credentials.** `login_attempts.name_key` holds
  `sha256("login-attempt\0" + name key)[:32]` instead of the typed name, so a
  token or password pasted into the name box is not kept; attempts past the
  15-minute window are deleted at the next login check. The uvicorn
  access/WebSocket log filter also masks the invite token in
  `/api/auth/invites/<token>` (`mask_path_secrets`), and `/api/auth/*`
  validation errors (422) no longer repeat the rejected `input`.
- The admin metrics page (`static/admin-metrics.html`) no longer refuses to
  ask the server while its token box is empty; it shows the box after a 401
  or 403.

### Reading older data

`login_attempts` rows written before this version are keyed by the plain
name; they match no digest, so they count for nothing and are removed at the
next login check (they were at most 15 minutes old to count anyway). Earlier
deletion records lack `"via"`. Metrics rows of an account deleted by the
older `--account` path are not recoverable and not orphaned (the folder
deletion removes them); a metrics row written for an account id that no
longer exists (a turn on a voice connection that was already open when the
account was deleted) stays until the folder is deleted.

### Going back

Code from before this version does not delete metrics or rename shared
names, and finds failed logins by the plain name: the lock does not see
attempts made under digests (they are gone within 15 minutes). The route
does not exist there. Nothing else reads the new record key.

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
| The `VOINEY_LAB_MOSS_ENABLED` row (nothing has read it since lane CL) and the collector's remaining `export` | `setting_names.py`, the then-existing collector |
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
- The safety-document operating guide no longer describes the search (current guide: `docs/DEPLOYMENT_RUNBOOK.md`).
  Documents that have to spell a deleted setting write it without the
  `VOINEY_LAB_` prefix (as earlier lanes did); the standard test command's
  `VOINEY_LAB_MOSS_ENABLED` (in `CLAUDE.md`, README and `docs/GIT_WORKFLOW.md`)
  is the one prefixed name documents may still spell
  (`tests/test_setting_names.py`).

Going back: code from before this version reads the same data; the deleted
settings were read only by the deleted code.

## Lane RP3 (2026-10-20), decision ①: the experiment report becomes a lab notebook

No schema change and no migration; nothing already stored is read
differently. The experiment record (`experiment_reports` and its events) is
unchanged; the notebook is a reading of it.

What changes for someone:

- The Word, PDF and Markdown exports (`/api/experiment-reports/{id}.docx|pdf|md`)
  are now the lab notebook: header (title = the purpose said, else the
  protocol's name; date and time; experimenter; protocol with the version run
  and its source; progress), "1. 시료와 조건", "2. 실제로 한 조건" ("원문 값 →
  실제", steps done as the source says grouped as "원문대로"), "3. 관찰과 결과",
  "4. 원문과 다르게 한 점·문제", "5. 지난번과 다른 점", "6. 다음에 바꿀 점·다음
  할 일", "7. 확인" (experimenter, PI), "8. 부록" (step times, the words as
  said with their corrections, the review version's changes, one line of
  system events, the source). The old sections 1–6, the first pages of lane
  RP2 (counts, timer and notices tables, the check's table) and the
  materials tables are gone from the body; the unconfirmed-value line and
  marks (lane RP2, decision 3) stay.
- The model-written sections of lane RP (purpose, background, methods,
  results summary, discussion suggestions, conclusion, next steps, item
  uses) are still prepared and kept in `experiment_report_prose` as before,
  but no longer printed: the notebook has no place for them. Decision ③ of
  this lane brings the AI-written notes ("AI 정리") in their place.
- Three event types are read when present and have Korean names in
  `EVENT_NAMES_KO`: `purpose_recorded` (목적·시료 기록), `result_recorded`
  (결과·다음 할 일 기록) and `pi_confirmed` (PI 확인). Nothing writes them yet
  (decisions ② and ④); a record without them reads "말하지 않음" and "PI
  확인: 아직 없음".
- "5. 지난번과 다른 점" reads the finished run of the same protocol that
  started last before this one from the same report store
  (`ExperimentReportStore.previous_finished_report`); no new table.

Going back: code from before this version reads the same data and prints
the old report.

## Lane RP3 (2026-10-20), decision ②: what the run asks for the notebook

No schema change and no migration. Three event types the notebook already
read (decision ①) are now written, and one setting is added.

- `purpose_recorded` (목적·시료 기록): the words said after the start's
  question "오늘 실험 목적이나 시료를 한 줄로 말씀해 주세요. 없으면 '넘어가'라고
  하세요.", with `payload.samples` (name, condition, control) read from the
  words deterministically. `result_recorded` (결과·다음 할 일 기록): the words
  said after the end's question "결과 한 줄과 다음에 바꿀 점이 있으면 말씀해
  주세요.", with `payload.result` and `payload.next_change` split at "다음에(는)".
  An observation said after "관찰한 것 있으면 말씀해 주세요." (asked once when a
  step whose source names what to observe is completed without a
  description) is an ordinary `observation` event at the completed step,
  category `observed`. "목적 고쳐: …" / "결과 고쳐: …" append a
  `record_corrected` event beside the saying it amends (lane N's shape).
- Every `observation` and `anomaly` event whose words name a sample ("3번
  샘플", "S2", "대조군") carries it as `payload.sample`.
- `VOINEY_LAB_NOTEBOOK_OBSERVATION_QUESTION` (default true) turns the
  observation question off. The start and end questions have no switch;
  "넘어가" skips each.
- None of the questions changes the workflow's state; a command said in
  their place runs as it does now. The session checkpoint tuple has one more
  trailing element (the open question and what was said), read as absent
  by older checkpoints.

Going back: code from before this version reads the same records; the new
events are unknown to it and ignored by its report.

## Lane RP3 (2026-10-20), decision ③: AI-written notes, the lab-meeting summary and the results table

No schema change and no migration. The report model's reply, kept in
`experiment_report_prose` as before (lane RP, decision 9), has a new shape;
two export formats are added.

- The model (the report role: `VOINEY_LAB_REPORT_PROVIDER` /
  `VOINEY_LAB_REPORT_MODEL`, off with `VOINEY_LAB_REPORT_WRITER_ENABLED=false`)
  now reads the notebook's facts only -- purpose, samples, the conditions
  used, the records, the differences from the source, the result and the
  change said, the values not confirmed -- and returns `observation_notes`
  (one sentence per step and sample) and `result_summary` (one paragraph).
  The old sections (purpose, background, methods, results, discussion,
  conclusion, next steps, item uses, general background) are no longer
  asked for, checked or printed; `check_report_sections`, `MODEL_SECTIONS`
  and the item-use checks are gone from `experiment_reports`.
- Each AI-written sentence is checked by the server (`check_ai_notes`):
  every number and unit must be in the record or the source, a value the
  experimenter did not confirm may not be stated, no step the record lacks,
  no identifier, no safety or procedural direction, no round count without
  "말로 확인한 돌아가기 기준", no speculation, no quotation that is not a
  record. One that passes is printed in "3. 관찰과 결과" marked "AI 정리 —
  N단계 · 시료: …" (and "결과 (AI 정리): …"); one that fails is dropped and
  the record's own words stand. The notebook's last line says how many
  sentences the model wrote, that the numbers were checked, and how many
  were dropped.
- A reply kept before this version (the old sections) is read as having no
  notes: the notebook prints the record's sentences and "AI 가 쓴 문장은
  없다". Asking for the prose again (the screen's button) writes it in the
  new shape.
- Two formats join `/api/experiment-reports/{id}.{format}`: `meeting-docx`
  and `meeting-pdf` (the one-page lab-meeting summary: 목적, 시료, 핵심 결과,
  지난번과 다른 점, 다음 할 일) and `xlsx` (the results table: one row per
  record with 시료, 과정, 조건 (원문 → 실제), 관찰, 측정값, 단위, 시각, 출처;
  the AI-written notes after the records, marked; the header on a second
  sheet). `openpyxl` was already a dependency.

Going back: code from before this version reads the same records; a reply
kept in the new shape has none of the old keys, so the old report prints
its server sentences.

## Lane RP3 (2026-10-20), decision ④: a PI's "확인함" on a notebook and on a review version

No schema change and no migration. Two append-only event types are
written; nothing waits on them and nothing is changed by them.

- `pi_confirmed` in the experiment record (`experiment_reports` events;
  Korean name "PI 확인" since decision ①): written by
  `POST /api/experiment-reports/{id}/pi-confirmations` with `{"comment": "…"}`
  (at most 300 characters), `payload` = the administrator account's name
  (lane ON), the one line, `"source": "screen"`; the time is the event's.
  Only an administrator account may post (403 `pi_confirmation_admin_only`);
  `GET …/pi-confirmations` and the report's public state
  (`pi_confirmations`) list them. The notebook's "7. 확인" prints each one
  (name, time, line); a second confirmation is appended, never replaced.
- `protocol_review_version_pi_confirmed` in the protocol ledger
  (`workspace_store` events of the protocol's revision): written by
  `POST /api/protocols/{id}/review-versions/{version}/pi-confirmations`,
  `payload` = version, name, comment, recorded_at. The version's record and
  the protocol's `review_versions` list carry `pi_confirmations`. The
  start is not held by it (lane DI: analysis passed + the experimenter
  starts).

Going back: code from before this version ignores both event types.

## Lane AN5 (2026-10-20): analysis finish -- one reading's mixed numbering, page-margin furniture, the re-ask after the cards

No schema change and no stored row rewritten; protocol store
(`protocol_workspace.sqlite`) only. Human decisions of 2026-10-20 (1-5).

- Decision 1: an analysis read at once (or one piece of a long document)
  may hold numbered and unnumbered steps, as the source prints them. Its
  review-pending payload's `flags` then hold lane AN3's seam items
  `{"item_id": "seam:<step>+<step>", "reason": "numbering_seam",
  "reason_code": "step_labelling_differs", ...}` for a single reading too
  (step ids without the `p<n>.` prefix); its review versions hold steps of
  both kinds, as AN3's joined documents' do. Nothing else is recorded.
- Decision 2: a quote that runs from one page's end onto the next may now be
  accepted past a page-margin line (a printed page number with the
  document's page offset, "200.8-24"; a line printed nearly alike in the
  outermost lines of another page; the copyright or licence footer of page
  1 or 2). It is stored in lane PA's form -- `source_excerpt` on its page,
  `continued_on_page_number` / `continued_excerpt` on the next -- each piece
  its own page's characters, the margin line in neither.
- Decision 3: the re-ask of lane AN4 runs after the analysis is stored.
  New append-only events:
  - `protocol_analysis_evidence_requote_started`: `{"status": "running",
    "analysis_id", "review_pending_event": <event id>, "time_limit_seconds":
    300}`, written right after the review-pending event; the summary's line
    ends " · 원문 위치를 다시 찾는 중" until the requote event below names the
    same `analysis_id` (or 420 s have passed: a server stopped under it).
  - `protocol_analysis_evidence_requote` (lane AN4's) gains
    `"review_pending_event"`, `"closed": "some" | "all" | "superseded"`
    when it closed cards (or found the target registered or read again
    meanwhile) and `"answered_by_person": [item ids]`; outcomes gain
    `answered_by_person`, `ocr_values_unsettled`; a call not back within the
    limit is `{"status": "failed", "failure_code": "requote_time_limit"}`.
    An analysis with more items than its cards may be is still asked about
    first, as lane AN4 did (no started event then).
  - `protocol_analysis_review_cards_closed`: `{"review_pending_event",
    "analysis_id", "closed": [{"item", "field", "page", "reason":
    "evidence_requoted", "reason_ko": "원문 위치를 다시 찾아 확인했어요"}]}`
    plus the record the review reads from then on, in the review-pending
    payload's own keys (`flags`, `needs_review_count`, `protocol`,
    `readiness`, `source_pages`, `auto_resolved`, ...). The review-pending
    event itself is not changed. `review_draft()` keeps each closed item's
    card in its place as `{"type": "requoted", "decided": true, "choices":
    [], "closed_ko": "원문 위치를 다시 찾아 확인했어요", "decision": {"action":
    "requoted", ...}}` (a person's own decision on it shows instead); the
    item carries `"closed": {"by": "server", ...}`. The protocol summary's
    `review_pending` gains `requoting`/`requoting_ko` while it runs and
    `closed_by_requote` after.
  - When the re-ask closes every card and nobody decided or wrote a memo
    on any, the analysis is stored as passed (`protocol_analysis_ready`,
    with the `evidence_requoted` entries in `auto_resolved`), as lane AN4
    stored it.
- Decision 5: each send of the analysis and translation roles writes one
  log line `provider.call provider=… role=… model=… status=… seconds=…
  output_tokens=… tokens_per_second=… finish=…` (no text). Nothing stored.

### Reading older data

Nothing older is read differently: an analysis stored before has no
started or closed event, and its cards are as they were. Checked on a copy
of `data/runtime/candidate-a-live-acceptance` (sqlite through the backup
API, objects hard-linked; the original only read): all seven protocols
list and review and the three that run load (`pdf-1-analysis-10` for the
in-gel fixture), the same as the code before this version.

### Going back

Code from before this version ignores the started and closed events: a
review-pending analysis whose cards the re-ask closed shows those cards
open again (as the analysis first read them); answering them registers as
before. A single reading's seam items and mixed review versions read as
lane AN3's do. A quote accepted past a margin line stays valid (each piece
is its page's own characters); only a step timer whose sentence runs past
such a page end is refused by the older timer check.


## 2026-10-21–23: source metadata, report ownership and start receipts

### Protocol catalog metadata (PERF)

The protocol store stays at schema 1. Registration appends
`protocol_source_metadata` with source identity and presentation metadata.
Startup fills absent metadata once; list reads do not append events, extract
PDFs or create another lab's entries. Existing sources and checksums remain
immutable. Verified immutable analysis records may be cached by source,
revision and record bytes; this is derived memory, not a second store.
Interrupted OCR is recorded as `protocol_ocr_interrupted`, permitting explicit
retry instead of leaving a permanent running status.

### Experiment report schema 1 → 2 (OWN)

The existing "Experiment record schema 1 → 2" section above is the
migration and rollback contract. The history index includes lab, owner,
protocol and start time; daily numbers, previous-run comparisons and
start-check answer projection share that scope. Unknown owners receive no
guessed history. `.agent/security_rules.md` 6절 holds the full permission
matrix. Workspace stays at schema 11, accounts and protocol catalog at 1.

### Account limits and erasure (OWN)

No account schema change. Login/current-password failures are counted by the
normalized name digest rather than a shared client address. The same network
does not lock other names, and forwarded address changes do not bypass a
name lock. Logout, password change, disable and deletion close all that
account's open voice connections. HTTP login-session scope remains specific
to each operation. Erasure drains in-flight voice report writes before making
the deletion inventory; no late worker is allowed to recreate deleted data.
Shared protocol bytes may be reused, while each lab's catalog/analysis/review
identity is separate. This also applies to starter packs.

### Account/revision-bound start confirmation (SAFE)

The protocol schema remains 1; answers still append
`protocol_start_check_answered`. New `protocol_start_safety_acknowledged`
receipts store the principal, the immutable `StartTarget` (source/analysis/
review identity), and the questions checked at that target. The browser gets
`target` from `GET /api/protocols/{protocol_id}/start-check` and submits it to
`POST /api/protocols/{protocol_id}/start-check/safety` after the value answers.
Invalid target types, stale targets and safety before completed values are
rejected. No numeric questions still requires safety acknowledgement.

With `VOINEY_LAB_START_CHECK_ENABLED` enabled (default), `session.start`
checks these receipts as well as catalog executability. Missing/wrong or
other-account receipts yield `session.configuration_required` with a reason
and Korean message before workflow/timer/report-start mutation. A later wrong
answer invalidates an earlier correct one. Recovery reads the original
execution revision's receipt; it never manufactures missing historical
receipts. Such an old run may need a new confirmed start. Disabling the setting
bypasses both value and safety receipt requirements. PI confirmation grants
no execution permission.

`workflow.resume` requires an already started run, matching configuration id
and generation. Repeated `session.start` on the same socket/configuration
keeps the run; a different configuration is visibly refused without closing
the socket. No event schema is inferred from prose.

### UI stages ②③ and pilot foundation

No database schema change. The screen consumes the safety target/receipt
contract, routes list/detail/upload and record list/detail separately, waits
for server end, and sends addresses through `serverUrl`/`serverSocketUrl`/
`serverFetch`. Logout or an expired session returns to login without a spent
invite. Public Drive/Docs uses the same upload lifecycle. Sheets remains an
API-only reagent table attachment. Offline extraction scoring and the blank
pilot comparison template write no workflow state and alter no execution rule.


### Pilot boundary follow-up: QA2 and UI4 (2026-10-23)

No stored schema version changes. Experiment recovery now preserves paused or
blocked status even when `reconnect` is absent and records recovery as
`session_recovered`; resume requires an explicit current control. Direct HTTP
experiment transitions return 409 `voice_control_required` after access/input
checks. Cancellation joins in-flight durable writes before rollback or another
control. Replaced voice connections cannot write observations or progress.

Browser origin checks now also apply with an empty app allowlist and compare
scheme, host and effective port. Cross-origin cookie writes are refused even
from app origins; the allowlisted bearer path remains. Accounts mode protects
API documentation as well. Account-admin metrics are lab-scoped; offline and
server-token whole-store summaries remain available. Missing usage scope and
malformed start-check/workspace booleans fail with the setting name.

`GET /api/workspace/experiments/{session_id}/reports` discovers existing reports
through both read gates without generating one. UI4 uses it for Word, Markdown,
JSON and CSV links in record detail. Account erasure is now connected to the
settings screen with password confirmation. Read-only records hide write
controls, and report numbering uses server values only. Extra export formats
and PI confirmation remain server-only.


### Public knowledge search (KNOW, 2026-10-24)

No stored schema version changes. Four settings are new: the provider names
`MOSS_PROJECT_ID` and `MOSS_PROJECT_KEY` (kept unprefixed, as Moss's SDK reads
them), `VOINEY_LAB_PUBLIC_KNOWLEDGE_ENABLED` (default false) and
`VOINEY_LAB_PUBLIC_KNOWLEDGE_INDEX` (default `voiney-public-knowledge`). The
package `moss==1.15.1` joins the dependencies and is imported only when the
tier is on. The public bundle and its raw downloads live under
`data/runtime/public-knowledge/` and are never committed; the ko/en glossary
`data/public_knowledge/glossary.json` is generated by
`scripts/public_knowledge_pipeline.py glossary` and committed. Nothing of the
Moss connection deleted on 2026-10-10 (approved-manual retrieval, reranking,
index sync) returns.

## Lane PRE (2026-10-24): the login lock by name and device

No account schema change (`accounts.sqlite` stays at version 1). The
`login_attempts.client` column, which held the peer address and was never
read by the lock, now holds `sha256("login-device\0" + marker)[:32]` of the
browser's device marker, or `unknown-device` for a request without one. The
lock counts failures per (name digest, device digest) -- five in fifteen
minutes lock that name on that device -- and per name digest -- thirty lock
the name everywhere. A success clears only that device's failures.

The browser receives the marker as the `voiney_device` cookie (random, 32
URL-safe characters, HttpOnly, SameSite=Lax, Secure over HTTPS, one year)
from `GET /api/auth/session`, signup and login in accounts mode; a missing or
malformed one is replaced. A listed app origin sends its marker as `device`
in the body of `/api/auth/login`, `/api/auth/password` and
`/api/auth/delete-account`; it gets no cookie. `AccountStore.log_in`,
`change_password` and `switch_off_for_deletion` take `device=` (the old
`client=` keyword is gone).

### Reading older data

Rows written before this version carry a peer address in `client`; they
count toward the name's thirty and toward no device's five, and leave the
store within fifteen minutes.

### Going back

The older code locks by name only and ignores `client`; the device cookie is
then unused and expires by itself.

## Workspace schema 11 → 12: the measured voice and the filter choice (2026-10-26, lane HEAR2)

Schema 12 is schema 11 with two more names in the `experimenter_settings`
CHECK: `speaker_filter` (the person's choice, `off` / `on`) and
`voice_reference` (what "목소리 맞추기" measured: `v1:<dBFS>:<unix
seconds>:<microphone id>`, written by `POST /api/voice/calibration` only).
Lane HEAR (2026-10-25) added both to `EXPERIMENTER_SETTING_VALUES` but wrote
no migration, so a workspace store at schema 11 refused the measurement's
row with an IntegrityError that the route answered as HTTP 500 — the ten
failed measurements of the person's test of 2026-10-26 (lane HEAR2 report,
section 1-5). Lane HEAR's tests ran with the workspace off and never met
it. Built the way schema 11 was: the rows are copied with their sequence
ids, the AUTOINCREMENT counter is put back, the index and both append-only
triggers are made again (`MIGRATION_11_TO_12`). Nothing else in the store
changes.

### Reading older data

A schema-11 store migrates on open; it holds no row of the two names, so
every voice is heard until the person measures and turns the filter on.
Checked on a copy of a schema-11 store built by the migrations in
`tests/test_lane_hear2_settings_store_v12.py` and through the production
route with the workspace on (`tests/test_lane_hear2_calibration_route.py`).

### Going back

Code from before this version refuses a schema-12 store ("Commercial
workspace schema is unsupported"): restore a backup taken before the
migration, or rebuild the table with schema 11's CHECK after deleting the
rows of the two new names.
