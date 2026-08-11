# Approved laboratory reference operations

This guide covers only the read-only reference tier. It does not approve a
Candidate A transition, change the curated fixture, or create ProcedureStore
state. Candidate A remains the execution authority for its own quantities,
ordering, and transition rules.

## 1. Prepare a reviewed manifest

Start from an explicitly selected document. Do not scan a directory. Record its
stable document ID, title, version, SHA-256, source language, document type,
authority, approval status, scope, effective/review dates, and sections. Mark
revoked or superseded versions inactive; never leave two active canonical
versions in one family/language.

Validate without writing a database:

```bash
.venv/bin/python -B scripts/audit_approved_catalog.py \
  --manifest /absolute/path/to/reviewed-manifest.json
```

Only a human-reviewed manifest with `approval_status: approved`, `active: true`,
and an intended non-test usage scope can support an answer. Runtime admission
also rejects titles/URIs/scopes marked fictional, demo, synthetic, test-only, or
non-operational; approval alone does not make such content usable guidance.

## 2. Build a candidate catalog outside production

The ingestion command writes the explicitly named SQLite file. Use a fresh
staging path and review it before changing any runtime configuration:

```bash
candidate_catalog="$(mktemp -d)/approved-lab-catalog.sqlite"
.venv/bin/python -B scripts/ingest_safety_documents.py \
  --manifest /absolute/path/to/reviewed-manifest.json \
  --db "$candidate_catalog"
```

Audit the staged catalog and run a representative Korean/English query:

```bash
.venv/bin/python -B scripts/audit_approved_catalog.py \
  --db "$candidate_catalog" \
  --scope reference_only \
  --query "2단계 acetonitrile 주의사항"
```

The audit prints identities and scores, never section text. A healthy database
is not enough: the intended question must return an active approved document and
the expected stable chunk identity.

## 3. Revoke, supersede, and re-ingest

Revocation is a reviewed manifest decision, not an in-place runtime toggle.
Create a new manifest revision, set the old document to `active: false` with
`approval_status: superseded` (or `rejected` when appropriate), add the reviewed
replacement with a new version/hash, validate it, and build a new staging
catalog. Confirm that a read-only query does not return the old chunk before an
operator atomically changes the configured catalog between server runs.

Do not edit a live SQLite file, mix stale and active versions, or use a generated
answer as approval evidence.

## 4. Runtime configuration and health check

The normal server configuration requires an absolute
`VOICE_WORKFLOW_AGENT_SAFETY_CATALOG`, an exact
`VOICE_WORKFLOW_AGENT_USAGE_SCOPE`, and (when policy requires it) a facility ID.
Configuration belongs in the existing operator environment; this guide does not
modify `.env`.

Before launch, run the audit command against the exact configured path and scope.
After launch, ask one related question whose answer is known to exist and verify:

- current-protocol facts are checked first;
- the Tool is shown only if approved reference retrieval actually runs;
- title, version, checksum, section/page, and chunk ID match the catalog;
- the answer is labelled additional approved guidance;
- the current Candidate A step does not change.

The current VM catalog audited on 2026-08-10 contains only two approved, active,
Korean `demo` records (`FICTIONAL-MOSS-DEMO-SDS-KO` and
`FICTIONAL-MOSS-DEMO-SOP-KO`, version 1.0). It is suitable only for the fictional
Moss demo. It cannot support operational Candidate A precautions until an
appropriate laboratory reference is separately reviewed and configured.

## 5. Optional Moss reranking

Moss is an optional reranker behind the SQLite approval gate. Follow
`docs/MOSS_RETRIEVAL.md`. The installed package is the `usemoss/moss` SDK; index
creation/updating uploads selected section text to Moss Cloud. Do not sync
laboratory content without explicit external-service approval. A disabled,
unavailable, or timed-out Moss runtime falls back to deterministic SQLite
ordering.

## 6. Optional authoritative web references

External reference search is disabled by default. Enabling it requires both the
feature flag and an explicit authoritative-domain allowlist. External results are
labelled non-protocol, cannot alter Candidate A state, cannot resolve Steps 7, 9,
or 20, and must retain their canonical URL and retrieval time.

The live xAI Responses adapter additionally requires:

- `VOICE_WORKFLOW_AGENT_EXTERNAL_REFERENCES_ENABLED=true` (or the documented
  `EXTERNAL_REFERENCES_ENABLED` alias);
- a reviewed domain profile such as
  `EXTERNAL_REFERENCE_DOMAIN_PROFILE=candidate_a`, or one
  to five comma-separated authority domains in
  `VOICE_WORKFLOW_AGENT_EXTERNAL_REFERENCE_DOMAINS`;
- a non-empty `VOICE_WORKFLOW_AGENT_EXTERNAL_REFERENCE_MODEL` (default
  `grok-4.5`);
- a bounded `VOICE_WORKFLOW_AGENT_EXTERNAL_REFERENCE_TIMEOUT_SECONDS` between
  1 and 30 seconds.

Each Turn is limited to one web-search request with SDK retries disabled. Returned
URLs are independently required to be HTTPS and inside the configured domains.
Google Custom Search and YouTube discovery are not part of this catalog or answer
path.

## 7. Candidate A usefulness gate

The local audit on 2026-08-10 found two active approved demo documents and three
active sections in `demo` scope. The representative Candidate A/acetonitrile
precaution query returned zero matches. Therefore the live local catalog is not
useful Candidate A evidence. Do not claim internal-RAG success until a real
reviewed laboratory manifest is staged, audited, and explicitly configured by
the operator using Sections 1–4.
