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
4. Confirm `schema_metadata.schema_version = 4` and exercise tenant login,
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
