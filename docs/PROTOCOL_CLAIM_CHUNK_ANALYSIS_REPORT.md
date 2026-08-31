# Evidence-first Protocol claim chunk prototype

## Outcome

The large-document path now uses an immutable, provider-neutral claim DTO. It
does not reuse the recursive `ExperimentProtocol` response schema for a chunk and
does not lower the 192 KiB chunk text limit. The pipeline is:

```text
page-bounded source
  → structural markers + scientific/execution claims
  → exact revision/hash/page/contiguous-excerpt validation per item
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

## Resource and latency policy

- Claim-chunk production routing is protected by the default-off
  `VOICE_WORKFLOW_AGENT_PROTOCOL_CLAIM_CHUNKS_ENABLED` deployment gate.
- The page planner uses at most eight core pages per chunk as well as the
  unchanged 192 KiB text ceiling.
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

## Legacy provenance debt

The existing `ScientificValue` type stores exact source text but does not embed
claim evidence. Canonical validated claim records therefore remain the
provenance authority, while evidence-bearing legacy fields carry revision/hash
identity through `location_detail`. A future explicit `claim_id` or provenance
reference from each legacy scientific value to its canonical claim would be
cleaner and less indirect than relying on `location_detail`; that domain-schema
redesign is intentionally outside this prototype task.

## Complexity review

`protocol_claim_analysis.py` is 1,572 lines after the ingress-size hardening. Its
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
independent source identities, non-contiguous/fabricated evidence, stale
revision/hash, exact page coverage, incomplete and missing chunks, deterministic
order, merge conflicts, no persisted candidate on failure, total-run timeout,
late-result fences, serial default, bounded concurrency two, one-page assembly,
page-count-based routing, and the real production catalog boundary.
