# Evidence-first Protocol claim chunk prototype

## Outcome

The large-document path now uses an immutable, provider-neutral claim DTO. It
does not reuse the recursive `ExperimentProtocol` response schema for a chunk and
does not lower the 192 KiB chunk text limit. The pipeline is:

```text
page-bounded source
  → deterministic source-bound evidence segments
  → structural markers + scientific/execution claims selecting adjacent segment IDs
  → server-owned exact-excerpt reconstruction and canonical validation per item
  → complete-set deterministic merge
  → whole-source coverage and reference consistency validation
  → existing ExperimentProtocol + readiness validation
```

Every chunk accounts for every core page exactly once. Context overlap pages may
help continuity but cannot contribute claims. A missing chunk, invalid item,
stale source identity, incomplete page, conflicting identifier, orphan parameter,
or unresolved action/section reference prevents assembly and persistence. The
catalog still requires explicit review, and existing readiness gates block
missing values and unsupported repeats/conditions.

Claim schema version 3 retains the version-2 removal of provider-authored
`source_excerpt` while compacting the provider boundary. Canonical segment IDs
remain SHA-256 identities over the segment schema/version, source revision,
document SHA, page number, page-text SHA, segment index, and segment-text SHA.
The provider sees only compact request-scoped handles and exact segment text.
An immutable server map resolves handles back to canonical identity and rejects
unknown, stale, cross-request, cross-page, reversed, duplicated, and
non-contiguous selections. Only server-resolved text and identity enter canonical
`SourceEvidence`.

## Resource and latency policy

- Claim-chunk production routing is protected by the default-off
  `VOICE_WORKFLOW_AGENT_PROTOCOL_CLAIM_CHUNKS_ENABLED` deployment gate.
- The page planner retains established windows of at most eight core pages and
  the unchanged 192 KiB text ceiling, then subdivides each window at a
  deterministic 4 KiB core-source target to bound expected output cardinality.
  An atomic source page is never split.
- The production default is one provider call at a time. Two-way concurrency is
  bounded and test-only/explicit until a live provider run over the real sources
  passes.
- The existing 120-second maximum is now one deadline for the complete run, not
  120 seconds for every batch. The timeout was not increased.
- Chunk result size, page count, total extracted text, retries, and concurrency
  remain hard-bounded.

## Local source prototype, 2026-08-31 UTC

`scripts/prototype_claim_chunks.py` ran the exact validation, merge, and assembly
path with a deterministic offline numbered-step extractor. This is an
architecture/source-integrity prototype, not evidence that a production model is
semantically complete.

| Source | Exact source identity | Pages | Chunks | Claims / numbered actions | Serial local total | Concurrency-2 local total | Result |
|---|---|---:|---:|---:|---:|---:|---|
| ANKOM leaf carbon fractions | `5367ca6bfae9fe9bbaeac9dab2099276a9c2dccf6c698ee36e59c7552e56d18a` | 40 | 5 | 126 / 67 | 1.753593 s | 1.751253 s | all chunks/evidence valid; `analysis_required` for retained repeat/multi-duration ambiguity blockers |
| Fictional short text-native lab protocol | generated `86919011e0762c696e83dddde0e89798f5c77bf188f3971effaf3639a77ec1f2` in this run | 1 | 1 | 4 / 1 | 0.002756 s | 0.002595 s | complete supported linear structure; `guidance_ready` before human review/approval |
| In-gel digestion protocol | `63d81102fb644fca21e1c2296b566987756f2964ece06758fe52c73ba9c00bd9` | 9 | 2 | 83 / 25 | 0.615610 s | 0.613081 s | all chunks/evidence valid; `analysis_required` for retained repeat/multi-duration ambiguity blockers |

The local pipeline is far below 120 seconds, but that does not substitute for
provider latency. A bounded real-provider ANKOM run used the normal application
configuration loader, `grok-4.6`, reasoning effort `high`, concurrency two, zero
retries, and the existing 120-second total deadline. Neither of the first two
active calls returned by the deadline; the remaining three planned calls were
never started. Provider wall time was 120.000258 seconds and total time including
PDF extraction was 121.806519 seconds. No response content or credential was
printed, no merge or readiness result was possible, and the timeout was not
increased. The operational target is therefore **not met** by this real run.
Concurrency two must not become the default, and neither real PDF can yet be
described as a real-provider-validated executable analysis.

### Low-effort representative-chunk follow-up

A subsequent controlled run selected ANKOM chunk ordinal 3, with core pages
25–32 and page 24 as context. It was selected as the hardest representative
shape: 19 numbered actions, 15 quantity patterns, 3 concentrations, 5
durations, 1 temperature, 20 repeat/conditional indicators, and 7
warning/prerequisite indicators. The request was 6,929 bytes, with a
conservative byte/3 estimate of approximately 2,310 tokens.

Exactly one `grok-4.6` request ran with reasoning effort `low`, concurrency one,
zero retries, and the existing 120-second request bound. It failed after
120.533248 seconds with `protocol_analysis_model_failed`; no structured response
reached parsing. Consequently there were no claims, page-coverage records, or
evidence results to accept. No second chunk or full ANKOM run was attempted, and
no prompt, schema, or evidence rule was weakened.

### Server-owned evidence-span follow-up

The next evidence audit used only privacy-safe metrics from the rejected
`grok-4.3` response. Its 193-character quote had the same length as the first
numbered action span on page 25, but its SHA-256 differed from the exact span,
from the line-break-to-space form, and from the existing canonical-whitespace
form. The source span was ASCII-only and the page was unchanged under
NFC/NFD/NFKC/NFKD. The exact supported classification is therefore a same-length
provider-authored non-verbatim content mutation, not a line-wrap, layout, or
Unicode-only variation. Because the retained artifact contains a quote hash and
length rather than plaintext, it cannot honestly distinguish a word-level
paraphrase from punctuation or equal-length word substitution.

Claim schema version 2 consequently makes excerpts server-owned. On 2026-08-31,
the revised representative request covered core pages 25–32 with page 24 as
context, 19 expected numbered actions, 123 deterministic segments, a 19,619-byte
claim request, and exact page reconstruction. Exactly one `grok-4.3` call used
reasoning effort `none`, concurrency one, zero retries, and the existing
120-second deadline. It returned no response before the client deadline:
provider latency was 120.518736 seconds and total call-plus-validation time was
120.523588 seconds. The result was `protocol_analysis_model_failed`; JSON/DTO
parse, claim counts, segment resolution, canonical validation, and complete
chunk status were all unavailable or false. No retry or second chunk was run.
This does not demonstrate the under-120-second target with valid evidence.

### Request-scoped evidence-handle compaction, 2026-09-01 UTC

Before changing the version-2 representation, the same ANKOM request was
measured without printing source content. The 19,619-byte claim input contained
9 pages (8 core plus 1 context), 123 segments, 13.666667 segments per page,
68-byte segment IDs on average, and 48.772358 bytes of segment text on average.
Its additive byte breakdown was:

| Component | Bytes | Conservative byte/3 estimate |
|---|---:|---:|
| System instructions | 2,192 | 731 tokens |
| Response JSON schema | 2,895 | 965 tokens |
| Page metadata | 211 | 71 tokens |
| Raw page source text | 5,999 | 2,000 tokens |
| Segment-ID properties | 10,209 | 3,403 tokens |
| Segment text/JSON representation overhead | 1,902 | 634 tokens |
| Repeated source/page/hash identity | 1,145 | 382 tokens |
| Other claim-request fields | 153 | 51 tokens |

The claim-input categories sum to 19,619 bytes. Segment identity and object
representation accounted for 12,111 bytes (61.731%); raw source text accounted
for 30.578%. The logical provider JSON payload, including messages and response
schema, was 26,300 bytes. Relative to the reconstructed 7,703-byte pre-span
claim input, version 2 had grown by 11,916 bytes (154.693%): 10,209 bytes came
from full ID properties and 1,707 from added per-segment JSON structure. This
identifies representation overhead, not provider-latency causation.

Version 3 groups text deterministically at filtered numbered-action boundaries,
with exact reconstruction and a 4,096-character maximum block size. No model is
used for segmentation. This keeps multiline actions, quantities, durations,
warnings, prerequisites, and repeat conditions in exact source blocks while
reducing this sample from 123 to 26 segments (78.861789%). The provider request
contains only `claim_schema_version`, capability policy, a 24-byte request
handle, and page records holding page number, core/context role, and ordered
`[18-byte handle, exact text]` pairs.

The full canonical segment identity remains server-only. The immutable request
map binds every compact handle to the full canonical ID, exact text, revision,
document SHA, page number, page-text SHA, and segment order. The request handle
is derived from the complete source/chunk/page/segment identity; each segment
handle is derived from that request handle plus its canonical segment ID.
Collisions within a request fail construction. Provider responses echo only the
request handle and cite page number plus segment handles. Canonical hashes and
excerpts are reconstructed by the server rather than trusted as provider echoes.

The resulting representative input was 7,390 bytes, a 62.332433% reduction from
19,619 bytes and 4.063352% below the 7,703-byte pre-span request. Its conservative
byte/3 estimate was 2,464 tokens. The response schema was 2,531 bytes, the system
prompt was 2,218 bytes, and the logical provider JSON payload was 12,798 bytes
(51.338403% below 26,300). The 26 provider handles totaled 468 bytes; average
segment text was 230.730769 bytes and the largest sample block was 740 bytes.
Exact page reconstruction remained true.

After the offline regressions passed, exactly one `grok-4.3` request covered core
pages 25–32 with page 24 as context, reasoning effort `none`, concurrency one,
zero retries, and a 119-second client timeout. No response arrived. Provider
latency was 119.556924 seconds and total call-plus-validation time was 119.558296
seconds; the exception chain terminated in `APITimeoutError`/`ReadTimeout`.
JSON/DTO parsing did not begin, so claim count, 19-action coverage, provider-handle
resolution, canonical evidence validation, and chunk completeness were
unavailable or false. The stop condition prohibited a retry or second chunk.
Payload compaction is demonstrated; under-120-second provider completion with
valid evidence is not.

### Output-cardinality follow-up, 2026-09-01 UTC

The deterministic provider fake produced one complete, canonically valid
18,378-byte response for core pages 25–32: 41 non-duplicate claims, 19 numbered
actions, 8 page-coverage records, and 41 evidence-handle references. The claims
array accounted for 16,968 bytes (92.328%); claim count predicted output bytes
more closely than page, action, or segment count in the measured 8/4/2-page
units. A conservative server-derivation-only DTO projection reduced bytes by
23.447%, while a 4 KiB core-source subdivision produced two complete valid units:
pages 25–29 at 9,301 bytes/20 claims and pages 30–32 at 9,232 bytes/21 claims.
Worst expected per-call bytes fell 49.391%; total expected bytes rose 0.843%.

Exactly one default-tier `grok-4.3` diagnostic then used the harder pages 25–29
unit with reasoning effort `none`, zero retries, and a 119-second total deadline.
Headers arrived at 1.205718 seconds and first output at 1.257477 seconds. The
stream emitted 12,195 non-empty deltas and 33,252 bytes through 118.993178
seconds but did not finish by 119.000928 seconds. No complete JSON reached parsing
or canonical validation. Smaller deterministic units are now enforced, but real
provider viability remains unproven and output generation remains the blocker.

## Legacy provenance debt

The existing `ScientificValue` type stores exact source text but does not embed
claim evidence. Canonical validated claim records therefore remain the
provenance authority, while evidence-bearing legacy fields carry revision/hash
identity through `location_detail`. A future explicit `claim_id` or provenance
reference from each legacy scientific value to its canonical claim would be
cleaner and less indirect than relying on `location_detail`; that domain-schema
redesign is intentionally outside this prototype task.

## Complexity review

`protocol_claim_analysis.py` is 1,980 lines after evidence-handle compaction. Its
size comes primarily from an explicit strict JSON schema, a manually decoded
immutable DTO boundary, independent post-construction revalidation, whole-source
consistency checks, and the deterministic adapter to the existing domain model.
Those layers intentionally repeat validation at ingress and merge so a forged or
stale in-memory DTO cannot bypass evidence checks. The module contains no test or
prototype model, ANKOM/vendor/filename rule, legacy compatibility branch, or
second executable protocol representation. The existing single-pass analyzer is
still a separate route for short/simple sources. No broad refactor was justified.

## Verification coverage

Offline regressions cover the small schema boundary, every claim category,
compact-to-canonical mapping, exact reconstruction, bounded action blocks,
fabricated/cross-page/cross-request/stale-revision handles, reversed and
non-contiguous ranges, exact canonical page coverage, incomplete and missing
chunks, deterministic order, merge conflicts, no persisted candidate on failure,
total-run timeout, late-result fences, serial default, bounded concurrency two,
one-page assembly, default-off page-count routing, the unchanged monolithic
route, and the real production catalog boundary.
