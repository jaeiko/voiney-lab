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
