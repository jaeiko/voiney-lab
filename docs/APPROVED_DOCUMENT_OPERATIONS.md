# Approved laboratory reference operations

Revised 2026-10-10 (lane CL). This guide builds the approved safety-document
catalog: the file `scripts/run_pilot.sh` requires before it serves (§4) and
that the step safety card reads. The voice answer from approved lab
references and the Moss reranker were deleted on 2026-10-10; what the catalog
is for next (and the pilot launcher's check) is decided in lane PL. The
catalog never changes a protocol: the protocol's source remains the authority
for its own quantities, ordering, and transitions.

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
`VOINEY_LAB_SAFETY_CATALOG`, an exact
`VOINEY_LAB_USAGE_SCOPE`, and (when policy requires it) a facility ID.
Configuration belongs in the existing operator environment; this guide does not
modify `.env`.

Before launch, run the audit command against the exact configured path and scope.
After launch, open a step whose safety card should carry a catalog excerpt
and check that its title, version and section match the catalog and that the
current step does not change. (Until 2026-10-10 a related voice question also
searched this catalog and answered from a matching section; that path was
deleted in lane CL.)

The current VM catalog audited on 2026-08-10 contains only two approved, active,
Korean `demo` records (`FICTIONAL-MOSS-DEMO-SDS-KO` and
`FICTIONAL-MOSS-DEMO-SOP-KO`, version 1.0). It is suitable only for a fictional
demo (CI builds the same records for its browser tests). It cannot support operational Candidate A precautions until an
appropriate laboratory reference is separately reviewed and configured, and the
pilot launcher refuses it because both records are demo documents.

### The controlled pilot's catalog

`scripts/run_pilot.sh` does not read either variable from `.env`. It fixes the
catalog at `data/runtime/pilot/approved_safety_catalog.sqlite` (an absolute
path under the checkout) and the scope at `reference_only`, the one scope a
pilot without OIDC can run; `README.md` ("Launchers") gives the reasons. Build
the catalog from a reviewed manifest in a staging path as in §2, audit it with
`--scope reference_only`, and copy it into place between server runs:

```bash
.venv/bin/python -B scripts/audit_approved_catalog.py \
  --db "$candidate_catalog" --scope reference_only
mkdir -p data/runtime/pilot
cp "$candidate_catalog" data/runtime/pilot/approved_safety_catalog.sqlite
./scripts/run_pilot.sh --check-only   # must exit 0
```

The launcher refuses to start, and `--check-only` exits 1, when the file is
missing or unreadable, holds any demo document (scope `demo` or `test_only`,
or a title containing "fictional", counted in any approval state), or has no
approved, active `reference_only` document. Every pilot document therefore
needs `usage_scope: reference_only`, `approval_status: approved`,
`active: true`, a non-`test_fixture` authority, and a title without
"fictional". Voice safety search returns only documents whose scope equals
the runtime scope, so a document in another scope would not be found by voice.

What the step safety card shows depends on the manifest:

- Each card line is the step's own PDF warning or an excerpt of one matched
  document, whole sentences only, about 240 characters at most and marked
  `…` where the section goes on. Nothing is paraphrased or translated by the
  server.
- A matched document contributes one section. Of its sections that matched
  the step, the card shows one whose `topic` is `hazards`; failing that, one
  whose `topic` is `handling` or `handling_storage`; failing that, the one with
  the lowest `page_start`. Within each of those, the lower `page_start` and
  then the lower `section_code`, read as numbers ("2" before "10"), decide.
  So give an SDS's hazard-identification section (section 2) the topic
  `hazards` to put it on the card, whatever else the manifest includes; voice
  search still uses every section.
- A document whose `review_due_at` has passed is left out of the card exactly
  as voice search leaves it out: none of its sections is shown and its names
  attach it to no step. Each document is judged by its own date, so an
  overdue translation disappears from beside its original, and a current
  reviewed translation of an overdue original is shown as a document of its
  own. Give a translation the same `review_due_at` as its original to have
  them leave together. The pack's `review_overdue_documents` lists the
  documents left out as `document_id:version`, and the server logs them; a
  date the server cannot read counts as passed.
- An SDS reaches a step only when the step's text names its `product_name`,
  one of its CAS numbers, or an alias with `approved: true` and
  `generic: false`. Record the names the protocol actually uses as approved
  aliases (for example `DTT` for a sheet titled "DL-Dithiothreitol", `AMBIC`
  for ammonium bicarbonate) and mark broad words such as "solvent" `generic`.
- An equipment manual reaches a step the same way: only when the step's text
  names its `product_name` (the machine), its `product_code` (the model), or
  an alias with `approved: true` and `generic: false`. A word such as
  "centrifuge", "vortex", "기계" or "설비" names no machine and attaches no
  manual. A manual for a machine missing from the protocol's equipment list
  (Candidate A's step 25 says only "speedvac") joins the pack only through
  such a name in a step, so record the name the protocol uses as an approved
  alias when the laboratory decides it means that one machine.
- A Korean translation is shown beside its original only when it is a
  separate document with `translation_status: human_reviewed`,
  `translation_of_document_id` naming the original, and the same
  `section_code`. A `machine_unreviewed` translation is never shown.
- A facility SOP whose topic or `document_id` contains "general" is put on
  every step and takes one of the card's three lines; the catalog has no field
  that limits a document to the whole session.

## 5. Moss reranking (deleted)

The optional Moss reranker was deleted on 2026-10-10 (lane CL), with its
settings and index sync script; it returns later for searching past records
and manuals. The record is `docs/archive/MOSS_RETRIEVAL.md`.

## 6. Optional authoritative web references

External reference search is disabled by default. Enabling it requires both the
feature flag and an explicit authoritative-domain allowlist. External results are
labelled non-protocol, cannot alter Candidate A state, cannot resolve Steps 7, 9,
or 20, and must retain their canonical URL and retrieval time.

The live xAI Responses adapter additionally requires:

- `VOINEY_LAB_EXTERNAL_REFERENCES_ENABLED=true`;
- a reviewed domain profile such as
  `VOINEY_LAB_EXTERNAL_REFERENCE_DOMAIN_PROFILE=candidate_a`, or one
  to five comma-separated authority domains in
  `VOINEY_LAB_EXTERNAL_REFERENCE_DOMAINS` (or its alias
  `VOINEY_LAB_EXTERNAL_REFERENCE_ALLOWED_DOMAINS`; set one, or both to the
  same value);
- a non-empty `VOINEY_LAB_SUPPLEMENTAL_MODEL` (default
  `grok-4.6`);
- a bounded `VOINEY_LAB_EXTERNAL_REFERENCE_TIMEOUT_SECONDS` between
  1 and 30 seconds (the Candidate A launcher uses a 20-second total deadline);
- optional 3-second connect and 15-second read deadlines through
  `VOINEY_LAB_EXTERNAL_REFERENCE_CONNECT_TIMEOUT_SECONDS` and
  `VOINEY_LAB_EXTERNAL_REFERENCE_READ_TIMEOUT_SECONDS`;
- an optional validated-result TTL through
  `VOINEY_LAB_EXTERNAL_REFERENCE_CACHE_TTL_SECONDS` (900 seconds in the Candidate A
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
conceptual dimension after the protocol, original source, and enabled
authoritative web tier do not answer it. It is never indexed into the
approved catalog and never gains document or URL citations.

```bash
export VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_ENABLED=false
export VOINEY_LAB_SUPPLEMENTAL_MODEL='grok-4.6'
export VOINEY_LAB_SUPPLEMENTAL_MODEL_KNOWLEDGE_TIMEOUT_SECONDS=8
```

The Candidate A development launcher enables this option so the live demo can
provide explicitly qualified general definitions when ordinary xAI text is
available but web search is not terminally useful. It remains ineligible for
safety controls, preparation instructions, substitutions, numerical operating
values, completion criteria, or any state mutation. The UI label is “일반 모델
설명 · 확인된 권위 근거 없음”; there is no citation list and no claim of
verification. Disable it to exercise a strict evidence-only run. (The screen now
labels this answer "AI 일반 지식" (lane U3) and the short outside-PDF
explanation "PDF 밖 설명 · AI 일반 지식" (lane R6).)

Research status is Turn/generation owned. Once a result reaches `success`,
`failed`, `timeout`, `cancelled`, `superseded`, or `unavailable`, neither the
server nor browser accepts another result for that identity. A newer accepted
Turn and session stop terminally close any older in-flight research operation.

The provider total deadline and visible enrichment budget are intentionally
different. Configure `VOINEY_LAB_EXTERNAL_REFERENCE_ENRICHMENT_BUDGET_SECONDS` below the
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
export VOINEY_LAB_MULTI_BRAIN_ENABLED=false
export VOINEY_LAB_ANSWER_MODEL='grok-4.6'
export VOINEY_LAB_ANSWER_BRAIN_PRIMARY_BUDGET_SECONDS=1.25
export VOINEY_LAB_ANSWER_BRAIN_TIMEOUT_SECONDS=8
export VOINEY_LAB_PLANNER_BRAIN_TIMEOUT_SECONDS=6
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
useful Candidate A evidence. (The voice answer this gate judged was deleted on
2026-10-10, lane CL; the catalog still feeds the step safety card and the
pilot launcher's check.)
