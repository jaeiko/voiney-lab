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
  `grok-4.6`);
- a bounded `VOICE_WORKFLOW_AGENT_EXTERNAL_REFERENCE_TIMEOUT_SECONDS` between
  1 and 30 seconds (the Candidate A launcher uses a 20-second total deadline);
- optional 3-second connect and 15-second read deadlines through
  `EXTERNAL_REFERENCE_CONNECT_TIMEOUT_SECONDS` and
  `EXTERNAL_REFERENCE_READ_TIMEOUT_SECONDS`;
- an optional validated-result TTL through
  `EXTERNAL_REFERENCE_CACHE_TTL_SECONDS` (900 seconds in the Candidate A
  launcher). Only cited, allowlisted success is cached.

Each Turn is limited to one web-search request with SDK retries disabled. The
adapter consumes Responses streaming events so tool start/end and first-event
timings remain observable, but its overall deadline is still hard-bounded and a
newer Turn cancels or rejects the old result. Returned URLs are independently
required to be HTTPS and inside the configured domains. Search is successful
only when the response reports a completed web-search tool and the admitted
citations support the returned claims.
Google Custom Search and YouTube discovery are not part of this catalog or answer
path.

### Supplemental model knowledge is not a retrieval backend

`SUPPLEMENTAL_MODEL_KNOWLEDGE` is a separately gated last resort for a narrow
conceptual dimension after the protocol, original source, approved catalog, and
enabled authoritative web tier do not answer it. It is never indexed into the
approved catalog and never gains document or URL citations.

```bash
export SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED=false
export SUPPLEMENTAL_MODEL_KNOWLEDGE_MODEL='grok-4.6'
export SUPPLEMENTAL_MODEL_KNOWLEDGE_TIMEOUT_SECONDS=8
```

The Candidate A development launcher enables this option so the live demo can
provide explicitly qualified general definitions when ordinary xAI text is
available but web search is not terminally useful. It remains ineligible for
safety controls, preparation instructions, substitutions, numerical operating
values, completion criteria, or any state mutation. The UI label is “일반 모델
설명 · 확인된 권위 근거 없음”; there is no citation list and no claim of
verification. Disable it to exercise a strict evidence-only run.

Research status is Turn/generation owned. Once a result reaches `success`,
`failed`, `timeout`, `cancelled`, `superseded`, or `unavailable`, neither the
server nor browser accepts another result for that identity. A newer accepted
Turn and session stop terminally close any older in-flight research operation.

The provider total deadline and visible enrichment budget are intentionally
different. Configure `EXTERNAL_REFERENCE_ENRICHMENT_BUDGET_SECONDS` below the
total timeout (the Candidate A launcher uses 4 and 20 seconds). Crossing the
shorter budget removes the primary spinner and reports bounded background work;
it creates neither a second request nor a second terminal result.

### Optional read-only multi-brain planning

The Candidate A launcher can enable the project-specific typed Answer, Source,
and Visual roles. They are internal LLM operations, not workflow function tools
or evidence backends. They receive a bounded immutable snapshot, cannot persist
or mutate state, and cannot make evidence-admission decisions. Keep them disabled
in an evidence-only run:

```bash
export VOICE_WORKFLOW_AGENT_MULTI_BRAIN_ENABLED=false
export VOICE_WORKFLOW_AGENT_MULTI_BRAIN_MODEL='grok-4.6'
export VOICE_WORKFLOW_AGENT_ANSWER_BRAIN_PRIMARY_BUDGET_SECONDS=1.25
export VOICE_WORKFLOW_AGENT_ANSWER_BRAIN_TIMEOUT_SECONDS=8
export VOICE_WORKFLOW_AGENT_PLANNER_BRAIN_TIMEOUT_SECONDS=6
```

The short primary budget is not a provider timeout. It lets admitted local text
and TTS proceed while a bounded Answer call may later add written detail. The
browser reports these as read-only brain diagnostics, never as fake Tools or
server operations.

The 2026-08-15 account probe used the application's actual OpenAI-compatible
`chat.completions` transport (the installed OpenAI client is 2.50.0). The
authenticated `/v1/models` list contained `grok-4.20-0309-non-reasoning` and
`grok-4.3`. The former passed Source and Visual but failed Answer admission; the
latter passed Answer and Source but returned `no_visual` for an explicit visual
request. No candidate was eligible for the concurrent acceptance probe. The
Candidate A launcher therefore keeps Multi-Brain disabled. This does not change
the separately configured Responses/web-search or supplemental models.

Current xAI documentation recommends Responses for new text integrations while
still documenting strict JSON Schema on Chat Completions. Migration of the
application role transport is a separate compatibility change; do not call the
deprecated transport live-verified merely because its schema request returned
HTTP 200.

## 7. Candidate A usefulness gate

The local audit on 2026-08-10 found two active approved demo documents and three
active sections in `demo` scope. The representative Candidate A/acetonitrile
precaution query returned zero matches. Therefore the live local catalog is not
useful Candidate A evidence. Do not claim internal-RAG success until a real
reviewed laboratory manifest is staged, audited, and explicitly configured by
the operator using Sections 1–4.
