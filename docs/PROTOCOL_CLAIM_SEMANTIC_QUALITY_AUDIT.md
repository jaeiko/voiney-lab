# Protocol claim semantic-quality audit

Status: **offline audit harness delivered and run; live-provider semantic
quality remains UNPROVEN.**

No provider call was made in this work. Every number below comes from the
deterministic offline fixture model in `scripts/prototype_claim_chunks.py`.
Nothing here is evidence about any live provider's semantic quality.

## 1. Recoverability of the previous audit run

The prior session's audit result is **not recoverable**, and no substitute has
been invented for it.

| Checked | Result |
| --- | --- |
| `scripts/diagnose_protocol_claim_latency.py` persistence | Writes nothing to disk; prints JSON to stdout only |
| Working tree / untracked / ignored paths | No `reports/`, no `outbox/`, no audit artifact |
| `git stash`, `git reflog`, `git fsck --lost-found` | No stash, no audit commit, no dangling blob |
| Recorded provider payload fixtures | None exist under `tests/` or `data/` |
| Repository-wide search for audit tooling | No semantic-audit code existed before this change |

The earlier run's output existed only in a terminal transcript that was lost.
Recovering it would require a new provider call, which has not been authorized
and has not been made.

## 2. What was built instead

`src/voice_workflow_agent/protocol_claim_semantic_audit.py` — a deterministic,
provider-agnostic semantic-quality audit, plus
`scripts/audit_claim_semantics.py` (offline runner) and
`tests/test_protocol_claim_semantic_audit.py` (19 tests).

Design constraints honoured:

- **Measurement, not a gate.** The module is not wired into admission. Canonical
  validation was not weakened, relaxed, or modified — `protocol_claim_analysis.py`
  and `protocol_chunk_analysis.py` are untouched by this change.
- **Source-derived expectations.** Every expectation comes from the immutable
  extracted page text, never from the claim set under audit and never from
  provider output. The same audit therefore applies unchanged to a live response.
- **No document-specific logic.** Detection is unit- and cue-driven. No ANKOM
  page numbers, action labels, quantities, section names, or fixed counts. An
  early draft that used domain nouns (`bags`, `crucibles`) was removed for
  exactly this reason.
- **One-sided error.** Every check reports only in the direction that stays
  valid under imperfect detector recall. The audit may miss a defect; it must
  never invent one. A test asserts that no finding quotes text absent from the
  source.

### Checks that survived validation

| Code | Severity | What it proves |
| --- | --- | --- |
| `value_not_represented` | critical | A step's source carries more distinct values of a category than the claims emitted for it |
| `value_span_not_isolated` | critical | A parameter claim's evidence span contains 2+ distinct values of its own category, so it cannot identify which one it asserts |
| `hazard_not_represented` | critical | An explicit hazard cue has no `warning_hazard` claim quoting it |
| `repeat_condition_not_represented` | critical | An explicit repeat cue has no `repeat_condition` claim |
| `prerequisite_not_represented` | advisory | A before-start cue has no `prerequisite` claim |
| `claim_lost_in_assembly` | critical | An admitted claim reaches no object in the assembled protocol |

### Checks deliberately removed after they proved unsound

These were implemented, measured against real source text, found to produce
false positives, and dropped rather than shipped:

- **Duplicate-claim detection.** Canonical evidence segments are whole
  numbered-action blocks, so two parameter claims on one step share
  byte-identical canonical source text. Nothing in canonical state distinguishes
  a duplicate from two genuinely different values. This is a property of
  evidence granularity, not an oversight — `value_span_not_isolated` measures it
  directly instead.
- **Surplus-claim detection.** Indistinguishable from the detector's own limited
  recall (protocols.io renders durations as colon-stripped `030000`, `1h30min`,
  `O/N`, and degree-less `60C`, which this module deliberately does not parse).
- **Value/category mismatch.** Same cause; every instance observed was the
  detector failing to recognise a value form, not a misclassified claim.
- **Step attribution.** `validate_whole_protocol_claims` already fails closed
  when a parameter claim's step does not match its target action. Re-checking
  would add nothing.

## 3. Offline results

Deterministic offline fixture model. **Not provider evidence.**

These counts score the offline stub, and the stub extracts every explicit
numbered step faithfully, so for the dimensions measured here it is an *upper
bound* on any provider working from numbered steps. The findings are therefore
**structural, not provider-quality**: a better model would produce the same
ones, because the request it receives cannot express the distinctions they are
about. See `docs/PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md` §2 -- reading them
as "a real model will do better" is the specific misreading to avoid. That
document also records (§1) a case where corrupted extraction produced duration
claims whose quoted evidence did not exist in the document and which
exact-evidence validation nevertheless admitted.

> **Superseded.** The table below is the original pypdf-era measurement,
> kept for the record. Two measurement faults were since repaired: a scorer
> that quoted corrupted evidence, and a census blind to `HH:MM:SS`
> durations. The current baseline is ANKOM 32 critical / 65-of-70 value
> tokens and in-gel 26 critical / 55-of-61 — see
> `docs/PROTOCOL_BOUNDARY_AND_OBLIGATION_DESIGN.md` §2a.

| Source | Pages | Chunks | Claims | Canonical admission | Critical findings | Chunks semantically clean |
| --- | --- | --- | --- | --- | --- | --- |
| ANKOM | 40 | 8 | 126 | passed | 21 | 4 / 8 |
| in-gel digestion | 9 | 3 | 83 | passed | 17 | 1 / 3 |

The headline result: **canonical admission passes on both documents while
execution-critical semantics are demonstrably missing.** Structural and
evidential validity does not imply semantic completeness.

Verified individually against the source text:

- ANKOM p30 carries `Danger, highly corrosive`, `ALWAYS ADD ACID TO WATER`,
  and required PPE. The claim set contains **zero** `warning_hazard` claims.
- ANKOM p3 `65°C` (a drying temperature in *Before start*) is represented by
  no temperature claim.
- ANKOM p31 `approximately 30 times` (a repeat count) has no repeat claim.
- in-gel p3 step 1 carries three distinct volumes (`1mm3`, `1.5ml`, `200 µL`)
  and one concentration (`25mM`); one quantity claim and no concentration claim
  were emitted.
- in-gel p5 step 5 `800 rpm` has no agitation claim.

Assembly preservation passed on both documents: every admitted claim reaches
the assembled `ExperimentProtocol` (0 lost).

## 4. Principal architectural finding

`value_span_not_isolated` fires 15 times on ANKOM and 12 times on in-gel, and
it is the most consequential result of this audit.

Canonical evidence segments are whole numbered-action blocks
(`generate_page_evidence_segments`). Since commit `642a76b` made source text
server-owned, a claim's `source_text` is reconstructed from those segments.
The minimum granularity is therefore **one entire numbered step**, so a
parameter claim cannot point at the value it asserts.

Observed downstream on in-gel step 12, where the source reads
`Incubate for 60min at 60C 800 rpm, 60°C`:

```
ProcessTimerSpecification.duration = ScientificValue(
    source_text='12 Incubate for 60min at 60C 800 rpm, 60°C, 01:00:00',
    parsed_value=None, normalized_unit=None)
```

The process timer's duration is the entire step paragraph rather than `60min`,
and three separate `ScientificValue` entries on that action carry byte-identical
text. A reviewer or an execution surface cannot recover the value.

This is **structural and provider-independent**: no provider, however good, can
select evidence narrower than one numbered-step block, so both banked
real-provider validations have this property too. Removing provider-authored
`source_text` was correct — it closed a paraphrase/fabrication hole — but
nothing replaced it with a server-derived *value span* inside the block.

Closing it means sub-block evidence spans or server-derived value offsets. That
changes the claim schema version and the provider handle contract, and would
invalidate the two banked real-provider validations, so it is **not** something
this change makes unilaterally. It is flagged for an explicit scoping decision.

## 5. Status and what remains

| Objective | Status |
| --- | --- |
| Prior audit result recoverable without a provider call | No — determined, not fabricated |
| Semantic audit completable from offline evidence | Yes — harness delivered and run |
| Live-provider semantic quality | **UNPROVEN** — requires an authorized call |
| Full-document live validation (40-page ANKOM, 8 chunks) | Not started |
| Reviewer/execution validation | Not started |

The offline run exercises the audit and the deterministic fixture. It says
nothing about how a real provider performs on these same checks. Establishing
that requires a provider call, which needs explicit authorization.

## 6. Verification

```
python scripts/replay_turns.py                              ok
python -m pytest -q                973 passed, 896 subtests passed (70s)
python -m compileall -q src tests scripts                   ok
git diff --check                                            ok
python scripts/audit_claim_semantics.py                     ok (no provider call)
```

Baseline flags `VOICE_WORKFLOW_AGENT_WORKSPACE_ENABLED=false` and
`VOICE_WORKFLOW_AGENT_EXPERIMENT_REPORTS_ENABLED=false` were forced off per the
documented pytest baseline; `.env` was not modified.

Audit findings quote laboratory source documents, so the runner is content-free
by default; `--include-source-excerpts` is opt-in and was used here only against
the two public protocols.io fixtures.
