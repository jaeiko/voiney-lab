# Segment boundary and completeness obligation — defect record and design

Status: **design and small-scale verification only.** No provider call was made
(0), no re-analysis was run, and no boundary or obligation change is
implemented. Measurements come from the two local sources under the
post-`2eff979` extractor.

## 1. Defect record: parser corruption was encoded into the extraction model

Replacing the extractor removed 8 claims from in-gel pages 4–8 and 11 from
ANKOM. Every one is a `duration` claim:

| Source | Page | Step | Category | Count |
| --- | --- | --- | --- | --- |
| in-gel | 4 | step-3 | duration | 2 |
| in-gel | 5 | step-8 | duration | 1 |
| in-gel | 7 | step-12 | duration | 1 |
| in-gel | 7 | step-16 | duration | 1 |
| in-gel | 7 | step-17 | duration | 1 |
| in-gel | 7 | step-19 | duration | 1 |
| in-gel | 8 | step-23 | duration | 1 |
| | | | **net** | **8** |

Cause, located exactly: `scripts/prototype_claim_chunks.py:76`. The offline
model's duration pattern is

```
[0-9]{2}[0-9]{2}[0-9]{2}
```

`U+E092` is pypdf's private-use substitute for a colon. **The parser's
corruption glyph was hardcoded into the pattern, with no real-colon
alternative.** With correct text (`00:15:00`) it no longer matches, so those
claims vanish. Line 70's temperature pattern is `[±]` — it accepts both
the real `±` and the corrupted one, which is why the two ANKOM temperature
claims survived as a clean swap (`102  2°C` → `102 ± 2°C`) rather than
disappearing.

The working hypothesis — that bare digit runs such as `003000` or `50491` were
being picked up as numeric claims — is **not** what happened. The matched
tokens were digit–PUA–digit sequences, not bare digits; `[0-9]{6}` does not
match `001500`.

The real defect is different and, in one respect, worse:

> Those duration claims quoted evidence that does not exist in the document.
> The value behind them was real (`00:15:00`), but the canonical `source_text`
> and `source_excerpt` contained `00<PUA>15<PUA>00`. Exact-evidence validation
> admitted them, because the corrupted extraction was self-consistent: the
> quote was genuinely present in the page text the server had, and it hashed
> cleanly. Byte-exact evidence against a corrupted extraction proves internal
> consistency, not fidelity to the document.

That is the invariant gap the dual cross-check in `2eff979` closes, and this is
the recorded instance of it having actually passed.

Nothing was fabricated out of nothing, and no step was lost: step labels are 69
(ANKOM) and 27 (in-gel), unchanged across the extractor swap.
`value_tokens_detected` rose (ANKOM 57 → 59), because the corrected text
exposes values the corrupted text hid.

## 2. What the audit numbers actually measure

`docs/PROTOCOL_CLAIM_SEMANTIC_QUALITY_AUDIT.md` reported 22 critical findings
for ANKOM and 17 for in-gel. Both figures are superseded — see §2a for the
current baseline. Those numbers score
`provider_mode = deterministic_offline_fixture` — the offline stub, not a
provider.

This matters for how they are read. The stub extracts every explicit numbered
step faithfully (69/69 and 27/27 labels, zero lost). For the dimensions the
audit measures, it is therefore **an upper bound on any provider that works
from numbered steps**: a real provider can match it but cannot do structurally
better, because the structure it would need is not in the request.

**These findings are therefore structural, not provider-quality.** Concretely:

- No hazard block, note, or before-start block is a separate accounting unit,
  so nothing anywhere records whether it was considered. That is a property of
  `_bounded_action_block_boundaries`, not of a model's judgement.
- A parameter claim cannot isolate the value it asserts, because the smallest
  selectable evidence unit is a whole numbered-step block.
- The completeness obligation is derived only from numbered labels, so an
  unnumbered block imposes no obligation on any model.

**Changing the model does not fix any of the above.** A better provider would
produce the same findings, because the request it receives cannot express the
distinctions the findings are about. Reading these numbers as "the stub is
weak, a real model will do better" is the specific misreading to avoid.

## 2a. Current audit baseline, after repairing the scorer and the census

Two measurement faults were fixed. Neither changed the pipeline; both changed
what the numbers mean, so the earlier figures are void.

**The scorer quoted corrupted evidence.** `scripts/prototype_claim_chunks.py`
carried pypdf's private-use substitutes inside its own patterns (§1). Both are
gone; no corrupted-glyph alternative was kept, because the current extractor
never produces one and a mismatch between engines now fails closed before
admission, so tolerating the artifact would only restore the blind spot.

Claim counts return to their pre-swap totals, but on clean evidence:

| Source | pypdf era | after extractor swap | after scorer repair | PUA in any canonical `source_text` |
| --- | --- | --- | --- | --- |
| ANKOM | 126 | 115 | **126** | **0** |
| in-gel | 83 | 75 | **83** | **0** |

The 83 is not a return to the earlier state. Then, 8 duration claims quoted
`00<PUA>15<PUA>00`; now the same 8 quote `00:15:00`. Verified: zero claims on
either source contain a private-use character, and 12 of in-gel's 16 duration
claims quote a real `HH:MM:SS` literal (the rest come from `15min` / `3 h`
forms).

**The census could not see clock durations.** `HH:MM:SS` carries no unit word,
so it did not fit the audit's number-plus-unit token shape. This was a silent
blind spot of exactly the shape that hides regressions: 8 duration claims
disappeared from in-gel while `value_tokens_represented` stayed at 46, because
those values were never counted in the first place. `_CLOCK_DURATION` now
matches the three-part form only — a two-part `H:MM` is ambiguous with a time of
day, and a ratio such as `(50:49:1)` cannot match because its final group is a
single digit.

**New baseline. These are the figures to compare against from here.**

| Source | value tokens (represented / detected) | critical findings |
| --- | --- | --- |
| ANKOM | **65 / 70** (was 54 / 59) | **32** (was 22) |
| in-gel | **55 / 61** (was 46 / 51) | **26** (was 17) |

| Code | ANKOM | in-gel |
| --- | --- | --- |
| `value_span_not_isolated` | 25 (was 15) | 20 (was 12) |
| `value_not_represented` | 4 | 4 (was 3) |
| `hazard_not_represented` | 2 | 1 |
| `prerequisite_not_represented` | 1 | 1 |
| `repeat_condition_not_represented` | 1 | 1 |

The rise is concentrated in `value_span_not_isolated` and is the blind spot
being removed, not a regression: 10 more ANKOM and 8 more in-gel steps are now
known to hold two or more distinct durations inside a single evidence span. They
always did; the census simply could not count them. Semantically clean chunks
fall to 2/8 (ANKOM) and 1/3 (in-gel). Claims lost in assembly remain 0.

## 3. Segment boundary redesign

### Offset mapping against the layout comparator: not feasible

poppler is already in the stack as the cross-check comparator, so layout output
is available at no new dependency cost. It cannot be used for boundaries.

The cross-check proves content equality as an **order-independent multiset**.
That is exactly what makes it robust against corruption, and exactly what makes
it useless for alignment. Measured, comparing the normalized non-whitespace
*sequence* per page:

| Source | Sequence identical | Divergent |
| --- | --- | --- |
| ANKOM | 24 / 40 pages | 16 |
| in-gel | 2 / 9 pages | 7 |

The divergence is reading order of superscript footnote markers
(`Ayotte1,EtienneLaliberté11` vs `Ayotte,EtienneLaliberté`) — the same
characters, placed differently.

**Correction to an earlier overstatement.** Full sequence identity is a
*sufficient* condition for a trivial index-walk alignment, not a necessary one
for alignment as such. An approximate alignment (a diff/LCS over the two
character streams, anchoring on the long agreeing runs) would very likely
succeed on most of these pages. The reason not to adopt it is therefore not
impossibility:

> An approximate alignment may well be achievable, but its output is not
> uniquely determined where characters repeat, and a boundary that lands in a
> different place depending on which of several equally good alignments the
> algorithm picks cannot support fail-closed evidence identity. Segment ids are
> hashed into every claim's evidence, so a non-deterministic boundary is a
> non-deterministic identity. It is rejected on the determinism requirement,
> not on feasibility.

Restricting boundaries to the pages where sequences agree exactly (24/40 and
2/9) would also reintroduce the silent-degradation problem it is meant to
avoid, and 2/9 coverage makes it useless for in-gel regardless.

**Conclusion: R2 alone, on the canonical pypdfium2 text.**

### R2 and what it costs

```python
boundaries = {0, len(text)}
           | {m.start("label") for m in _numbered_action_matches(text)}   # unchanged
           | {m.end() for m in re.finditer(r"[.!?]\s*\n", text)}          # new
# existing 4096 cap loop unchanged
```

| Rule | ANKOM max/pg · total | in-gel max/pg · total |
| --- | --- | --- |
| current | 5 · 93 | 9 · 31 |
| **R2** | **18 · 174** | **11 · 56** |
| layout blank+indent (unreachable) | 18 · 390 | 22 · 117 |

The loss versus layout is granularity: 174 segments instead of 390 on ANKOM.
Layout would additionally separate a block heading from its body and split
inside long note paragraphs. It also splits wrapped sentences at indent
changes, which R2 does not — so the comparison is not one-sided.

ANKOM p30 under R2, on the corrected text, is the case that matters:

```
[ 2]  '50 Prepare a 72% H2SO4 solution or take a 250 ml bottle of 72% H2SO4.'
[ 4]  'Safety information Danger, highly corrosive.'
[ 5]  'Exothermic reaction ... ALWAYS ADD ACID TO WATER (slowly) AND NOT THE OPPOSITE!'
[ 6]  'Wear gloves, labcoat, safety glasses.'
[ 7]  'Work under the chemical hood.'
[ 9]  'Use a cylinder to measure 242 ml of dH2O and pour in the beaker.'
[11]  'Use a glass cylinder to measure 758 mL of H2SO4 and SLOWLY pour into the beaker.'
[12]  'Wait at least 1 h to for the solution to cool down.'
[13]  'In a 1 L glass cylinder, adjust the final volume to 1 L with dH2O.'
```

The hazard block becomes four independent segments; each preparation value
becomes its own segment. Segment `[5]` keeps the wrapped line
`ALWAYS ADD ACID TO WATER` / `(slowly) AND NOT THE OPPOSITE!` together, which
is what R2 buys over splitting at every newline.

### Monotonicity and the 4096 cap

R2 only **adds** boundary offsets; it never removes one. Verified on both
sources: per-page segment counts under R2 are `>=` the current counts on every
page. R2 therefore cannot be worse than today's segmentation for any document,
and cannot guarantee separation either — a page with no sentence-terminating
line break degrades to today's behaviour.

The 4096 cap (`_MAX_PROVIDER_SEGMENT_CHARS`, `:44`) runs after the coarse
boundaries. Because R2 only adds boundaries, maximum segment length can only
shrink; measured, the cap fires on **no page** of either source under R2
(ANKOM p30's longest segment drops from 740 to 240 characters). It must stay as
the hard guard for the degenerate case — a page whose extraction contains no
newline at all, which is precisely what the synthetic test fixtures produce.

## 3a. R2 as implemented — measured outcome

Implemented in `_bounded_action_block_boundaries` as one added term,
`_SENTENCE_LINE_END = re.compile(r"[.!?]\s*\n")`.
`EVIDENCE_SEGMENT_VERSION` 3 → 4; `CLAIM_SCHEMA_VERSION` stays 5, since the
response shape is unchanged.

**Monotonicity, proven per page.** Every boundary the label-only rule produced
survives, on all 40 ANKOM pages and all 9 in-gel pages: 0 violations. 34 of 40
and 8 of 9 pages gained boundaries. The 4096-character ceiling fires on no page
of either source, and is retained as the guard for a newline-free extraction —
exercised by a test that builds exactly such a page.

### The boundary change alone moved nothing, and why

| | ANKOM | in-gel |
| --- | --- | --- |
| `value_span_not_isolated`, R2 only | 25 → **25** | 20 → **19** |

R2 worked at the layer it targets: ANKOM p30 went from 3 segments to 18, with
the hazard block isolated as four of them. The metric did not follow, because
it measures the span a *claim* cites, and the offline scorer attached
`action_evidence` — the whole step block — to every parameter claim
unconditionally (`prototype_claim_chunks.py:271`). No boundary refinement can
move a claim that always cites the entire step.

That is an instrument fault of the same class as the hardcoded corruption
glyph: the scorer was not modelling a provider that cites its own value.
Canonical validation permits the narrow citation — a parameter claim's
`source_text` need only be a substring of its target action's excerpt — so a
faithful provider would make it. The scorer now selects the segments holding
its own token, located by absolute page offset within the step so it cannot
land in a neighbouring one.

### Measured outcome with the instrument repaired

| | ANKOM | in-gel |
| --- | --- | --- |
| `value_span_not_isolated` | 25 → **10** (−15, −60%) | 20 → **18** (−2, −10%) |
| critical total | 32 → **17** | 26 → **22** |
| chunks semantically clean | 2/8 → **4/8** | 1/3 → 1/3 |
| claims | 126 (unchanged) | 83 (unchanged) |

ANKOM p30, the case this was built for — every preparation value now cites one
segment of its own:

```
quantity-p30-0-4  segs=1  'Use a cylinder to measure 242 ml of dH2O and pour in the...'
quantity-p30-0-5  segs=1  'Use a glass cylinder to measure 758 mL of H2SO4 and SLOW...'
duration-p30-0-1  segs=1  'Wait at least 1 h to for the solution to cool down.'
quantity-p30-0-6  segs=1  'In a 1 L glass cylinder, adjust the final volume to 1 L ...'
quantity-p30-0-3  segs=1  'Note Under the chemical hood, place a stirring plate, 2 ...'
```

**Correction to an earlier overstatement in this section.** An earlier revision
said the hazard block was "no longer part of step 50's evidence". That was
wrong, and conflated segmentation with citation. The hazard did become separate
*segments*, but `action-p30-0` still cited all thirteen of them —
`[2,3,4,5,6,7,8,9,10,11,12,13,14]` — because the scorer attached the whole step
block to the action claim. Only `[0]`, `[1]` and `[17]` were uncited. The
hazard was still inside the action's evidence, so nothing had been separated
where it counted. §4a records what fixed it.

### Why in-gel barely moved — an inherent limit, not a defect

Every one of in-gel's remaining 18 findings is **two or more same-category
values on a single line**:

```
12 Incubate for 60min at 60C 800 rpm, 60°C, 01:00:00      <- two durations, one line
10 Prepare a solution of 1.5mg/mL of DTT 10 millimolar ...  <- five concentrations, one line
```

R2 splits at line ends. It cannot separate values that share a line, and no
line-based rule can. ANKOM's notes spread values across lines, which is why it
gained 15; in-gel packs them into step trailers, which is why it gained 2. The
honest statement of R2's reach is therefore: it separates content across lines,
and leaves within-line packing untouched. Closing the remainder needs
sub-line evidence spans, which is a larger change than this one and is not
attempted here.

## 4. Obligation set redesign

Current: `:1816` `if not set(numbered_labels).issubset(action_labels)`, with the
obligation derived only from `_numbered_step_labels` (`:1244`).

Proposed: **segment-level exhaustive accounting.** Every substantive segment on
a core page must be accounted for exactly once — either at least one claim or
marker cites it, or the provider returns an explicit `no_claim` disposition for
it.

- **`numbered_action_missing` (`:1822`) is retained unchanged** as a separate,
  stricter invariant. This is deliberate: it preserves the existing reason code,
  its diagnostic, and the two tests that assert it.
- **Substantive**, deterministically and without vocabulary: the segment
  contains at least one `[A-Za-z0-9]` character. Measured, this exempts 1
  segment out of 174 on ANKOM and 0 out of 56 on in-gel — the `.` orphan on
  p30. Cheap, and it never fires on real content.
- **Server cross-check using `_VALUE_UNITS` (`:58`)**: a segment containing a
  unit-bearing number cannot be declared `no_claim`. Units are SI/scientific
  notation, not document vocabulary, so this adds no label dictionary.

  One implementation note, **corrected after measurement**: `_VALUE_UNITS` is a
  lowercase set, and an earlier draft of this document claimed it therefore
  "misses `758 mL` and `2 L`". That is wrong about production. Its only
  consumer, `_numbered_action_matches` (`:691`), casefolds the candidate token
  before the set lookup, so `mL`, `µL` and `L` are all recognized correctly.
  Measured on both sources: **zero** false positives and **zero** false
  negatives attributable to unit case — no line beginning with a unit-bearing
  number is admitted as a step label. The lowercase-only form is correct for its
  current use and is not a live defect.

  The case question applies only to *reusing* the set in a new, case-sensitive
  matcher. There, matching case-sensitively would miss `758 mL`, and matching
  case-insensitively conflates `mM` with `mm`. For this cross-check the
  conflation is harmless, because the check needs only a boolean — "does this
  segment carry a unit-bearing number?" — never the unit's identity. So the new
  check should casefold, exactly as the existing consumer does, which raises
  forced segments from 32 to 41 (ANKOM) and 16 to 24 (in-gel). The semantic
  audit's *category assignment* still needs case sensitivity; different
  question, different answer.

  Separately observed and not a case issue: `24 crucibles` is admitted as step
  label `24`, because the guard only rejects a following *unit*, not a following
  noun. Recorded, not addressed here.

### What this covers without any new rule

| Category | Covered | Why |
| --- | --- | --- |
| quantity, duration, temperature, concentration, agitation | **YES** | the unit cross-check forces a claim on any segment carrying a unit-bearing number |
| numbered actions | **YES** | existing invariant retained |
| prerequisite | **PARTIAL** | ANKOM p3's before-start segment contains `72 h` and `65°C`, so it is forced. A prerequisite with no numeral is only accounted for |
| materials | **PARTIAL** | `Catalog #A6141` has digits but no unit, so it is accounted for, not forced |
| warning / hazard | **PARTIAL** | the segment now exists and must be dispositioned, but nothing proves `no_claim` is wrong for it |

Measured on ANKOM p30: segments `[4]`–`[7]`, the entire hazard block, carry no
unit-bearing number, so `no_claim` remains permissible for them. Forcing a
hazard claim would require hazard wording, which is out of scope by
construction.

**What is not covered:** execution-critical prose with neither numeral nor unit
— `Danger, highly corrosive`, `ALWAYS ADD ACID TO WATER`, PPE lists. These gain
*accounting*, not *enforcement*.

That is still a real improvement over today, where the hazard text is absorbed
into step 50's segment and **no accounting unit for it exists at all**. After
the change it is a distinct segment that the provider must either claim or
explicitly decline, and the declination is recorded, reviewable, and
attributable to a specific segment id by the existing audit. A silent loss
becomes a recorded decision.

## 4a. Action-claim citation, and all-segment accounting as implemented

### Narrowing the action claim

The principle already applied to value claims — cite only the segment holding
your own text — now applies to action claims. An action cites from its label to
the end of its own instruction sentence, not to the end of its step block.
Measured on ANKOM p30:

| | `action-p30-0` cites | Uncited segments |
| --- | --- | --- |
| before | `[2 … 14]` | `[0] [1] [17]` |
| after | `[2]` | `[0] [1] [3] [4] [5] [6] [7] [10] [14] [16] [17]` |

The hazard block `[4]–[7]` and the unnumbered execution note `[0]` are now
outside every claim's citation. `numbered_action_missing` is unaffected: it
compares numbered labels against action `source_label` values, not evidence
extents, and the whole suite still passes.

### What had to change to allow it

A parameter claim was validated as a substring of its target action's excerpt.
That rule *forced* the action to quote its whole step: otherwise nothing
attached to the step could satisfy it. It is replaced by containment in the
**step block** — the page range from a numbered label to the next one, derived
server-side by `step_block_ranges`. A step block is territory, not a
quotation. The action quotes its instruction, a warning quotes itself, and both
are checked to lie inside the same block. This is what makes a hazard claim
citing only the hazard segment expressible at all; under the old rule it was
rejected.

### The obligation

`_validate_page_segment_accounting`: on every core page, each **substantive**
segment (one `[A-Za-z0-9]` character, no vocabulary) is either cited by at
least one claim or marker, or listed in that page's declination list — never
both, never neither. `CLAIM_SCHEMA_VERSION` 5 → 6, because the response now
carries `declined_evidence_segment_ids` per coverage record. Declined handles
are resolved server-side through the same page-bound handle map claims use, so
a declination cannot name a segment from another page.

Reason codes: `segment_unaccounted`, `segment_claimed_and_declined`,
`declined_segment_not_on_page`, `declined_segment_states_a_value`,
`duplicate_declined_segment`, `unknown_evidence_handle`.

`ANALYSIS_INCOMPLETE` is exempt from accounting and blocks the whole-document
merge instead. That is deliberate: "I could not finish this page" is the one
honest answer that cannot also promise per-segment accounting, and it is now a
visible refusal rather than the page-wide silence it replaces.

### The unit cross-check

A segment stating a number with a unit cannot be declined. `_VALUE_UNITS` is
matched case-insensitively, exactly as the numbered-line guard does, so
`758 mL` and `2 L` are recognized; conflating `mM` with `mm` is harmless because
the check only answers whether a value is present, never which unit it is.

## 4b. Did it work? Measured, including where it did not

**Yes for accounting, partially for safety.** On ANKOM p30 the hazard segments
are now explicitly declined rather than silently absorbed:

```
p30  CLAIMED    [2, 8, 9, 11, 12, 13, 15]
     DECLINED   [0, 1, 4, 5, 6, 7, 10, 14, 16, 17]
     UNACCOUNTED [3]        <- ". " only, non-substantive, correctly exempt
```

Segment `[0]`, the unnumbered note carrying execution content, is caught too —
it is now on the record as declined instead of invisible.

**What the obligation does not do is force a hazard *claim*.** Segments
`[4]`–`[7]` carry no unit-bearing number, so `no_claim` remains permissible for
them. This is the PARTIAL outcome predicted in §4 and it is the honest limit:
forcing a claim there would need hazard wording, which is excluded by
construction. What changed is that the decision is now recorded, attributable
to a specific segment, and reviewable — a silent loss became a stated one.

**The obligation found 15 real under-extractions.** Segments that state an
execution value, are not claimed, and therefore cannot be declined:

| Source | Examples |
| --- | --- |
| ANKOM (10) | p3 `72 h at 65°C` (Before start), p10 `Weigh 20 g of Sodium sulfite`, p13 `20 g Na2SO3 4.0 mL alpha-amylase` |
| in-gel (5) | p5 `800 rpm, 37°C, 00:15:00`, p8 `Overnight digestion with trypsin 16h`, p9 `soak the gel piece in 10uL` |

The offline model cannot satisfy this, and the reason is a **second structural
finding**: a quantity, duration or temperature claim must target an action,
material or equipment claim, and a value stated outside any numbered step has
no such target. The claim model has no way to express it. The model therefore
marks those pages `analysis_incomplete`, and the whole-document merge refuses
both documents with `incomplete_source_coverage`. That refusal is the correct
outcome and is now reported by the audit runner as
`whole_document_merge: rejected`, which still emits per-chunk and
whole-document findings so a refused document can be examined.

## 4c. Findings by risk class

Counting one `critical` total mixes different things. From here the audit is
reported in three lines. `value_span_not_isolated` is *imprecise but not
wrong*, and a fall in that number alone is not an improvement in safety.

| Class | Codes | ANKOM | in-gel |
| --- | --- | --- | --- |
| **Safety** | `hazard_not_represented` | **2** | **1** |
| **Execution** | `prerequisite_not_represented`, `repeat_condition_not_represented`, `value_not_represented` | **7** | **5** |
| **Precision** | `value_span_not_isolated` | **10** | **18** |

Safety is unchanged by this step, and saying so plainly is the point: narrowing
citations and adding accounting did not extract a single additional hazard. What
they changed is that the hazard is now a named, declined unit rather than
invisible.

## 4d. Degraded pages and the obligation

On a page that yields one segment, accounting is satisfied by one claim, so the
obligation is one unit wide and effectively vacuous — the concern raised when
the detector was reinstated.

Decided: the unit cross-check still applies there, so a degraded page stating a
value cannot be declined, and `degraded_segmentation_pages` is reported
alongside the findings so a reviewer can see which pages have one-unit-wide
accounting. It is **not** escalated to a refusal. Both measured degraded pages
are a cover page and an equipment metadata list; refusing them would block
every document at its front matter, and forcing a claim would pressure
invention. Recording that a page's accounting is weak is the strongest
defensible position that does not do either.

## 4e. A place for content outside the numbered steps

STEP 4 left both documents refused at merge: 15 segments stated an execution
value, could not be declined, and could not be claimed either, because a
quantity or duration must target an action, material or equipment claim and a
value outside every numbered step has none.

### The change, and why it is not a new document structure

No new claim category and no new response field. A value claim may carry
`target_claim_id = null` **if and only if** its evidence lies outside every
numbered step block on its page. `_outside_every_step_block` decides that from
the immutable page text, so a provider cannot declare a claim document-level to
escape a target it actually needed — the one test that matters here is that the
same claim placed inside a step block is rejected with `claim_target_invalid`.
A document-level claim must also carry no section, step or label.

`CLAIM_SCHEMA_VERSION` **stays 6**: `target_claim_id` was already nullable, so
the response shape is unchanged. `EVIDENCE_SEGMENT_VERSION` stays 4; boundaries
did not move.

Assembly maps these to `BeforeStartPrerequisite` with a `condition-` prefix,
reusing an existing domain type rather than adding one. That matters because a
claim reaching no domain object is invisible to a reviewer and to execution;
`claims_lost_in_assembly` is 0 on both documents, so they do surface.

### Result

| | ANKOM | in-gel |
| --- | --- | --- |
| segments stating a value, unclaimed and undeclinable | 10 → **0** | 5 → **0** |
| whole-document merge | rejected → **accepted** | rejected → **accepted** |
| claims | 126 → 132 | 83 → 86 |

All 15 are expressible. Each of the six named cases, verified individually:

| Case | Now expressed as |
| --- | --- |
| ANKOM p3 `72 h at 65°C` | `temperature-outside-p3-0`, document-level |
| ANKOM p10 `20 g` | `quantity-outside-p10-1`, document-level |
| ANKOM p13 `4.0 mL` | `quantity-outside-p13-0`, step-attached |
| in-gel p5 `800 rpm 37°C 00:15:00` | step-attached claims on that step |
| in-gel p8 `16h` | step-attached claims on that step |
| in-gel p9 `10uL` | `temperature-outside-p9-0`, document-level |

One imprecision worth recording rather than hiding: the offline model picks the
first parameter pattern that matches anywhere in the segment, so in-gel p9's
volume was labelled `temperature`. That is a scorer flaw, not a rule flaw, and
the audit's own category checks are the place it should surface.

### A false positive the unit cross-check had to lose

`Catalog #I1149-5G` matched as "5 grams" and `224-1S SKU` as "1 second", so six
catalog lines were undeclinable for no reason. The cross-check now rejects a
number that continues a hyphenated code. This is about the shape of a code, not
about any word. Cost: `2-mm screen` is no longer treated as a standalone
measurement, which is acceptable for a hyphenated adjectival form.

## 4f. Hazard claims: the rule no longer blocks them, the model still cannot make them

**Expressible: yes, proven.** On ANKOM p30, a `warning_hazard` claim citing
only its own segment is admitted in both forms, for all four hazard segments:

```
seg[4] 'Safety information Danger, highly corrosive.'   step-attached ADMITTED  document-level ADMITTED
seg[5] 'Exothermic reaction ... ALWAYS ADD ACID TO WATER'                ADMITTED                ADMITTED
seg[6] 'Wear gloves, labcoat, safety glasses.'                           ADMITTED                ADMITTED
seg[7] 'Work under the chemical hood.'                                   ADMITTED                ADMITTED
```

Before `6977a70` the step-attached form was impossible: a claim attached to an
action had to be a substring of that action's excerpt, so a hazard could only
be admitted by making the action quote the whole step. That rule limit is gone.

**Produced: no.** The offline model emits zero hazard claims, on any page.

**Which limit is it now?** A model limit, not a rule limit. Recognising that
"Danger, highly corrosive" is a hazard while "Start stirring (slow speed)" is
not is a semantic judgement. Nothing deterministic and vocabulary-free can make
it, and a hazard word list is excluded by construction — it was removed from
this codebase in `a1774b0` for inverting its own purpose.

**Consequence for the safety gate, stated plainly.** Because no protocol yet
produces a hazard claim, `NO_DECLARED_SAFETY_WARNINGS` fires on every document.
That is the alarm-fatigue failure argued against when a prerequisite gate was
rejected: a gate that always fires makes its acknowledgement a formality, and a
formality is worth nothing on the day it matters.

Avoiding it needs a provider that performs the classification — the gate's
premise is that *some* protocols genuinely declare no hazard, and that premise
is currently untested because *no* protocol declares one. Establishing whether
a real provider clears the gate on ANKOM p30 requires an authorized provider
call, which has not been made. Until then the gate is honest but
undiscriminating, and that should be understood as the current state rather
than as a working control.

## 4g. Declinations are recorded but not visible

ANKOM p30 segments `[4]`–`[7]`, including `ALWAYS ADD ACID TO WATER`, are now
explicitly declined. Checked where that reaches:

| Surface | Carries the declination? |
| --- | --- |
| `ProtocolChunkClaimAnalysis.page_coverage[].declined_segment_ids` | **yes** |
| Stored analysis revision | **no** — `page_coverage` is not persisted; the store keeps the assembled domain protocol and readiness |
| `review()` payload | **no** — no `page_coverage`, no declination key |
| Reviewer screen | **no** |

So for a reviewer, "recorded" is currently indistinguishable from "still
invisible". The declination exists only inside the chunk analysis during
processing. Persisting `page_coverage` with the analysis revision and surfacing
it in `review()` is the missing link; UI and persistence work was out of this
step's scope and is not done.

## 4h. The authorized provider call: one request, decisive on the question asked

One call, no retry. Model `grok-4.3`, reasoning effort none, chunk 6 of 8 —
core pages 30-32, context page 29, 28 handles, 3,730-byte request, 3,461-byte
schema. Latency **14.2 s**, response **8,651 bytes**, 29 claims.

**A pre-flight caught a defect before the call was spent.** The schema *required*
`declined_evidence_segment_ids` while the prompt never mentioned it, so the
model would have been forced to emit a field it had no instruction for. The
prompt was corrected first, with no hazard wording added, so the experiment
stayed a test of the model's own judgement.

### The question this was for: does a real provider make hazard claims?

**Yes.** Six `warning_hazard` claims, four of them on page 30, covering **all
four** hazard segments, each citing exactly its own segment:

| Segment | Claim | Form |
| --- | --- | --- |
| `[4]` `Safety information Danger, highly corrosive.` | `c-30-2` | document-level |
| `[5]` `Exothermic reaction ... ALWAYS ADD ACID TO WATER` | `c-30-3` | document-level |
| `[6]` `Wear gloves, labcoat, safety glasses.` | `c-30-4` | document-level |
| `[7]` `Work under the chemical hood.` | `c-30-5` | document-level |

The offline model produced zero on the same page. That difference is the whole
answer to §4f: the limit was the model, and a real one clears it.

### Canonical validation nevertheless rejected the response

Fail-closed, for two independent reasons, and the validator was not relaxed to
let either through.

**1. `section_id` was a human title.** The marker returned
`section_id = "Lignin method in beakers"`; `_STABLE_ID` forbids spaces. This is
a **contract defect, not model judgement**: the schema types `section_id` as a
plain nullable string with no `pattern`, so strict structured output could not
enforce the character set, and the prompt never stated it. The model answered
reasonably to an under-specified contract.

**2. One segment was left unaccounted.** With the identifier slugged locally
for analysis, validation then refused `segment_unaccounted` on page 30 segment
`[0]` — `Note If not proceeding immediately with the acid lignin method ...`.
Everything else accounted: 16 of 17 substantive segments on page 30, and pages
31 and 32 complete. This one is **model judgement**, and the accounting
obligation caught it. It is the same segment §4b highlighted as newly visible.

Because validation failed, no analysis revision exists, so no *validated*
provider result is claimed here. The hazard finding above is reported as
captured provider output, not as admitted canonical state.

### The safety gate would still have fired — and should

All four hazard claims are **document-level**. `declared_safety_warning_count`
counts only step- and action-attached warnings, and a document-level hazard
assembles into `before_start`. Verified on a constructed protocol: the
document-level form leaves the count at 0 and
`NO_DECLARED_SAFETY_WARNINGS` fires; the step-attached form clears it.

The gate is **not** being widened to accept the document-level form. Its
original reasoning holds: a hazard that never attaches to a step is not read
out at the moment the operator is mixing acid, so it does not discharge the
execution-time obligation. The hazard block on page 30 sits inside step 50's
territory and is expressible in the step-attached form; the provider chose the
document-level one. That is a steerable claim-structure choice, not a licence to
loosen the gate.

So the gate's premise is now partly tested: a real provider *does* produce
hazard claims, which was the open question, but not yet in the form the gate
requires. Establishing whether it will do so when asked needs another
authorized call, which has not been made.

## 4i. Declinations now persist and reach the reviewer

`page_coverage`, including `declined_segment_ids`, is stored with the analysis
revision and exposed in `review()` as `page_coverage` plus a
`declined_segment_count` summary. It is held as plain JSON rather than tagged
domain records, so the store stays ignorant of the claim module's types, and
every record is validated at ingress.

`ANALYSIS_SCHEMA_VERSION` is unchanged: the field is optional on read, so an
analysis stored before it existed still deserializes. Measured storage cost:

| Source | Coverage records | Declined segments | Payload |
| --- | --- | --- | --- |
| ANKOM | 40 | 80 | 249,157 → 268,766 bytes (**+7.9%**) |
| in-gel | 9 | 22 | 134,612 → 140,425 bytes (**+4.3%**) |

A reviewer can now read which segments a provider declared it had no claim for.
Rendering it is a UI change and was out of scope.

## 4j. Known limitations

Two costs of earlier decisions, recorded rather than left implicit.

**The offline scorer mislabels a value's category.** It picks the first
parameter pattern that matches anywhere in a segment, so in-gel page 9's volume
`10uL` was emitted as `temperature`. The claim's evidence is correct and the
value is real; only the category is wrong. It is a scorer flaw, not a pipeline
flaw, and the semantic audit's category checks are where it should surface.

**`2-mm screen` is no longer read as a standalone measurement.** Excluding a
number that continues a hyphenated code was necessary to stop
`Catalog #I1149-5G` being read as five grams and `224-1S` as one second, which
made six catalog lines undeclinable for no reason. The cost is that a
hyphenated adjectival measurement is now invisible to the unit cross-check. The
trade was six false positives against one construction that is rarely the
operative value, and it is a deliberate choice rather than an oversight.

## 4k. Rule parity: enforced and declared must be the same set

The principle this section applies: every rule the server enforces belongs in
the schema or the prompt, and every rule the prompt states belongs in the
server. A rule on one side only is a defect, and the two failure modes are
different. Enforced-but-undeclared means a provider cannot satisfy it and the
call is wasted, which is what happened to `section_id`. Declared-but-unenforced
means it can be ignored without consequence, which is what happened to warning
attachment.

### Audit

| Rule | Was | Now |
| --- | --- | --- |
| `structure.marker_id` matches `_STABLE_ID` | enforce-only | both |
| `structure.section_id` matches `_STABLE_ID` | enforce-only | both |
| `claims.claim_id` matches `_STABLE_ID` | enforce-only | both |
| `claims.section_id` matches `_STABLE_ID` | enforce-only | both |
| `claims.step_id` matches `_STABLE_ID` | enforce-only | both |
| `claims.target_claim_id` matches `_STABLE_ID` | enforce-only | both |
| `claims.source_label` non-empty | enforce-only | both (schema `minLength: 1`) |
| A value claim is document-level only outside every step block | enforce-only | both |
| A warning attaches to the step whose span holds it | **neither** | both |
| Every substantive segment is cited or declined, exactly once | declare-only wording, enforced without a countable check | both, with an explicit count instruction |
| `source_order` non-negative | both | both |
| Evidence page-local, contiguous, adjacent | both | both |
| Numbered action completeness | both | both |
| Unique `claim_id` / `marker_id` per chunk | enforce-only | enforce-only — a provider cannot check global uniqueness from one chunk, so declaring it would not help |
| "Every scientific or execution fact must be its own claim" | declare-only | declare-only — a semantic instruction with no deterministic test; kept as guidance, not claimed as a control |

Seven field-level parity defects were closed by declaring `_STABLE_ID` in the
schema, so strict structured output now rejects a bad identifier at the provider
instead of the response failing decode. Two rule-level defects were closed by
declaring the positional attachment rule and the accounting count check. Two
entries are deliberately left one-sided, with the reason recorded rather than
papered over.

### The attachment rule, enforced by position and nothing else

`warning_must_attach_to_enclosing_step`: a `warning_hazard` claim whose evidence
lies inside a numbered step's span must target that step's action claim; one
whose evidence lies outside every step stays document-level. Verified offline in
both directions on ANKOM page 30, whose two step blocks are `(150, 881)` and
`(881, 1341)`:

| Case | Document-level | Step-attached |
| --- | --- | --- |
| segments `[4]`-`[7]`, inside step 50 | **rejected** `warning_must_attach_to_enclosing_step` | **admitted** |
| page 3 segment `[0]`, no step blocks on the page | **admitted** | n/a |

The rule reads offsets, never text. Nothing in the code or the prompt names a
hazard: what counts as one stays the provider's judgement, and this constrains
only where the resulting claim may sit. The prompt wording, verbatim:

> A claim attaches by position. A numbered step spans from its own label up to
> the next numbered label, and owns everything in between. If a claim's evidence
> lies inside that span it must target that step's action claim, so it is
> delivered at the step it governs. Only a claim whose evidence lies outside
> every numbered step is document-level, and only that claim leaves
> target_claim_id null. This applies to warning_hazard exactly as it does to a
> quantity or a duration.

A test asserts the prompt contains none of `danger`, `corrosive`, `hazardous`,
`toxic`, `flammable`, `caution`, `irritant`, `explosive`,
`protective equipment` or `safety information`.

## 4l. Second authorized call, against the first

One call, no retry, same chunk: core pages 30-32, context page 29.

| | STEP 6 (`b4aa151`) | STEP 7 |
| --- | --- | --- |
| latency | 14.2 s | **12.9 s** |
| response | 8,651 bytes | **6,627 bytes** |
| claims | 29 | 24 |
| `warning_hazard` claims | 6 | 6 |
| of those, **step-attached** | **0 of 6** | **6 of 6** |
| page 30 hazard segments `[4]`-`[7]` | 4 document-level | **4 step-attached to `c50`** |
| `section_id` accepted | **no** — prose title | **yes** |
| page 30 accounting | 16 of 17 | 15 of 17 |
| canonical validation | rejected | rejected |

**The attachment change worked.** Every hazard claim moved from document-level
to step-attached without a single hazard word entering the prompt. Position was
sufficient.

**The identifier parity fix worked.** No identifier in the response violates
`_STABLE_ID`; the failure that consumed the first call did not recur.

**Canonical validation still rejected the response,** on accounting alone:
`segment_unaccounted` for page 30 segments `[0]`
(`Note If not proceeding immediately with the acid lignin method ...`) and
`[1]` (`Lignin method in beakers`). The model returned **zero** structure
markers this time, against one in STEP 6, so nothing cited the section heading
that `[1]` holds. Accounting was 15 of 17 substantive segments, against 16 of 17
before, with the explicit count instruction in place — so this is model
judgement, not an undeclared rule. The validator was not relaxed.

Adding only those two declinations locally, chunk validation **passes** with 6
hazard claims, 6 step-attached, and coverage
`p30=complete, p31=complete, p32=analysis_incomplete`. That is analysis of the
captured response, not a provider result.

**The safety gate was not exercised, and is not reported as cleared.** The
claims are now in the form the gate counts — step-attached warnings reach
`sub_action.warnings`, which `declared_safety_warning_count` counts, verified by
construction in §4h. But the chunk never reached assembly: accounting failed,
and the model marked page 32 `analysis_incomplete` on its own, which blocks the
whole-document merge regardless. The gate needs an admitted whole-document
analysis to be exercised, and this call did not produce one.

## 4m. Replay now asserts

`replay_turns.main` dumped JSON and returned 0 unconditionally, with no
assertions. Every "replay OK" in this work therefore established only that
nothing raised. Rather than drop it from the verification list, it now checks
what it can honestly check, because the routing smoke path is genuinely worth
having:

- every requested turn produced a record, with the expected turn id
- each record carries a non-empty `intent`, `action`, `runtime_router` and
  `answer_origin`
- `state_mutation` is a boolean, and a turn claiming a mutation must show the
  step actually moving
- no turn is silent

Checks are shape-and-invariant rather than golden output: pinning exact speech
would freeze wording that is allowed to change, while a turn that silently
produced no route is a real regression. Failures print to stderr and return 1.
Tests cover each failure mode plus the real demo replay passing its own check.

## 4n. Segment [0]: expressible, and missed anyway

Page 30 segment `[0]` is
`Note If not proceeding immediately with the acid lignin method, place the
filter bags in the dessicator until the next step.` It lies outside both step
blocks on that page and carries no unit-bearing value, so it is declinable and
the unit cross-check does not reach it.

Every claim category was tried against it, document-level and step-attached:

| Category | Document-level | Step-attached |
| --- | --- | --- |
| material, equipment, prerequisite | **admitted** | rejected `top_level_claim_scope_invalid` |
| quantity, concentration, temperature, duration, agitation_speed | **admitted** | rejected `action_claim_scope_conflict` |
| warning_hazard, observation_checkpoint, repeat_condition | **admitted** | rejected `action_claim_scope_conflict` |
| explicit_missing_ambiguous_value | **admitted** | rejected `action_claim_scope_conflict` |
| action | rejected `action_structure_invalid` | rejected `action_structure_invalid` |

**Twelve of thirteen categories admit it.** `prerequisite` is the semantically
apt one - a conditional instruction to satisfy before continuing - and
`repeat_condition` or `observation_checkpoint` are defensible. The
step-attached rejections are correct: the segment is outside every step, so it
cannot attach to step 50.

**This is not defect #14.** It is not the same class as #12 (a hazard could not
be expressed, because the substring rule blocked the step-attached form) or #13
(a value outside every step had no target). Those were rule defects, where the
contract made a true statement unsayable, and both were fixed structurally. Here
the contract is willing and the statement is sayable twelve ways over. The
failure is **model recall on the ledger**, established by construction rather
than inferred.

### The content type, and why the same type split into two defect classes

The type is stable and worth naming: **unnumbered, value-free, execution-bearing
prose.** A hazard block is one instance; a conditional storage instruction is
another. What differs is not the content but which side failed.

For a hazard, the rule was wrong and the model was right - it wanted to make the
claim and could not, or could only in the form the safety gate ignores.
Position-based enforcement fixed it, and the model has produced 6 of 6
step-attached hazards on two consecutive calls.

For segment `[0]`, the rule is right and the model omits it. Three consecutive
calls, three misses, never once declined either. The server has no lever: the
only deterministic handle on value-free prose is the unit cross-check, and by
construction this text carries no unit. Forcing a claim would need to know what
the prose means, which is the judgement we deliberately do not encode.

So the type is one type, and it now has one solved half and one unsolved half.
The unsolved half is a recall problem, not an expressibility problem.

## 4o. `[0]` and `[1]` are different failures

Reported separately because the causes and the fixes differ.

**Segment `[1]`, `Lignin method in beakers` - variable, now resolved.** A section
heading. STEP 6 cited it with a marker whose `section_id` was the prose title
`Lignin method in beakers`, which `_STABLE_ID` refused. STEP 7 emitted no
structure marker at all, so nothing cited it. STEP 8 emitted
`m1` with `section_id='sec-lignin-beakers'` - a slug - and the heading is cited.
The variability was the model probing an under-declared contract; declaring
`_STABLE_ID` in the schema removed the ambiguity, and no `[1]`-specific handling
was added or needed.

**Segment `[0]` - consistent, unresolved.** Missed on all three calls. Nothing
about the contract prevents it. No action is taken beyond making the failure
non-fatal and precisely recorded, because the available levers are either
ineffective (the unit cross-check cannot see it) or dishonest (a vocabulary that
guesses which prose matters). It is recorded as an open model-recall limitation.

## 4p. Judgement and bookkeeping: keep them together, change the failure mode

Across three calls, semantic judgement was stable and bookkeeping was not:
hazard identification produced 6 claims every time, and page 30 accounting went
16, 15, 16 of 17. The proposal considered was to move the ledger to the server -
have it auto-decline any substantive segment the model left out, keeping the
unit cross-check so value-bearing segments still fail closed.

**The case for.** The server can compute the complement set exactly, so asking
the model to restate what the server already knows spends output budget on
redundant work. A section heading or a page footer carries no execution meaning.
Rejecting a chunk over one unlisted heading discards 6 correct hazard claims and
11 correct action claims, which is a poor trade against the actual risk. And the
two responsibilities demonstrably have different reliability profiles, which
usually argues for different owners.

**The case against, which decides it.** The carve-out does not cover the class
that matters. "No unit-bearing value, therefore safe to auto-decline" is exactly
the assumption that failed for hazards: `Danger, highly corrosive` carries no
unit and is the most execution-critical text on the page. Under the proposal a
hazard the model *missed* - as opposed to declined - would be auto-declined by
the server and become invisible again, which is precisely the defect this line
of work spent five steps making visible. Segment `[0]` proves it concretely: it
carries execution content, carries no unit, and would be auto-declined. A
detected failure would become an undetected one. The current behaviour is also
not a false alarm; "this page's ledger is incomplete" is true and actionable.

**Not adopted.** Bookkeeping stays with the model.

**What changed instead.** An omission and a contradiction were being treated
identically, and they are not the same fault. Claiming and declining the same
segment, or declining one that states a value, is an active false statement and
still fails closed. Leaving a segment out is a silence: the server now records it
against the exact segment ids in a new server-derived
`unaccounted_segment_ids`, forces that page to `analysis_incomplete`, and lets
the chunk stand. The whole-document merge refuses `analysis_incomplete` exactly
as before, so nothing reaches execution and silence still does not pass - but the
6 correct hazard claims survive for review instead of being thrown away with the
one missing heading. The behaviour is declared in the prompt, per the parity
principle:

> A segment you neither cite nor decline is recorded against you and forces that
> page to analysis_incomplete, which stops the document being approved, so
> account for it rather than leaving it out.

## 4q. Third authorized call, three-way

| | STEP 6 | STEP 7 | STEP 8 |
| --- | --- | --- | --- |
| latency | 14.2 s | 12.9 s | **21.2 s** |
| response | 8,651 B | 6,627 B | **9,806 B** |
| claims | 29 | 24 | **33** |
| `warning_hazard` | 6 | 6 | **6** |
| step-attached | 0 of 6 | 6 of 6 | **6 of 6** |
| structure markers | 1 (prose `section_id`) | 0 | **1 (slug)** |
| page 30 accounting | 16 of 17 | 15 of 17 | **16 of 17** |
| segment `[0]` | missed | missed | **missed** |
| canonical validation | rejected | rejected | **passed** |

Canonical validation passes for the first time. Page 30 is
`analysis_incomplete` with `unaccounted_segment_ids = [0]`, which is the new
recorded-omission path doing its job: the chunk is admissible and reviewable,
and the whole-document merge still refuses.

Hazard attachment held at 6 of 6 across two consecutive calls, so the positional
rule is not a one-off.

### Obligations carried by this single request

Kept as evidence for a later decision about splitting judgement across calls.
Nothing is split now.

| Obligation | Result | Satisfied |
| --- | --- | --- |
| numbered action extraction | 11 action claims | yes |
| value extraction | 8 value claims | yes |
| hazard judgement | 6 claims, 6 step-attached | yes |
| declination list | 3 segments declined | yes |
| **exhaustive accounting** | **page 30 short by segment `[0]`** | **no** |

Four of five obligations were met. The one failure is the bookkeeping one, on
the same segment, for the third time - consistent with the reliability split
above, and the reason the record now names which obligation fell short rather
than reporting a single pass or fail.

## 4r. First whole-document run: the real baseline

Eight authorized calls, one per chunk, no retry, against `4a9f130` with no code,
prompt or schema change beforehand. Three earlier calls had all landed on chunk
6, which passed each time; this is the first measurement of the other seven.

**Two of eight chunks passed.** Six failed, for six different reasons.

| Chunk | Core pages | Latency | Response | Result | Cause |
| --- | --- | --- | --- | --- | --- |
| 0 | 1-4 | 4.2 s | 1,694 B | rejected | `coverage_mismatch` p3 - page marked `complete` with nothing claimed |
| 1 | 5-8 | 8.0 s | 4,074 B | **passed** | 4 segments recorded unaccounted |
| 2 | 9-16 | 12.6 s | 6,658 B | rejected | `declined_segment_states_a_value` p13 - declined `20 g Na2SO3 4.0 mL alpha-amylase` |
| 3 | 17-23 | 17.2 s | 7,788 B | rejected | `coverage_mismatch` p18 - `complete` with nothing claimed |
| 4 | 24 | 7.9 s | 4,940 B | **passed** | fully accounted, 18 claims |
| 5 | 25-29 | 12.4 s | 6,796 B | rejected | `declined_segment_states_a_value` p26 - declined a note stating `2 h` |
| 6 | 30-32 | 25.6 s | 9,579 B | rejected | `numbered_action_missing` p32 - label 55 not claimed |
| 7 | 33-40 | 16.2 s | 9,245 B | rejected | `segment_claimed_and_declined` p37 - one segment both cited and declined |

Chunk 6 is the one that had passed three times. On its fourth run it failed for
a reason none of the earlier runs hit. Run-to-run variance is high enough that a
single passing chunk was never evidence about the document.

### Document totals, from the raw responses

Counted from provider output rather than admitted state, since six chunks were
refused.

| | Count |
| --- | --- |
| claims | 142 |
| action | 83 |
| value (quantity, duration, temperature, agitation) | 43 |
| **warning_hazard** | **9** |
| **of those, step-attached** | **9 of 9** |
| equipment / material / prerequisite / other | 17 |
| structure markers | 23 |

Hazard claims landed on pages 4, 16, 25, 30, 32 and 36. Only page 30 matches the
audit's hazard cue pattern, and it did receive a claim, so **no page with source
hazard text lacks a hazard claim** by that measure. The model also found hazards
on five pages the cue list does not flag, which is direct evidence that a
vocabulary would have under-detected and is the reason none was added.

### The 13 unaccounted segments, classified

This classification is the basis for deciding whether a second pass is worth
building.

| Kind | Count | Examples |
| --- | --- | --- |
| **Execution information** | **6** | p3 `Before start ... dried for 72 h at 65°C`, p5 `NOTE: A running average blank bag correction factor (C1)`, p10 `Weigh 20 g of Sodium sulfite and keep it for step 16.`, p19 `Detach both cubitainers from ports A and B.`, p30 `Note If not proceeding immediately ... dessicator`, p32 `Note If acid remains in the filter bags ...` |
| Heading, footer or catalog metadata | 6 | 3 running footers (p5, p6, p8), 3 equipment/catalog lines (p33, p37, p39) |
| Ambiguous | 1 | p33 `carefully.` - a wrapped fragment |

Six of thirteen carry execution information. Segment `[0]` of page 30 is now
missed on four consecutive calls, and page 3's before-start drying condition is
missed again, so this is not a rare event confined to one page: it is spread
across six pages in five different chunks. The footers and catalog lines are the
class a second pass would not need to reason about at all.

### Obligation success across eight chunks

Measured per obligation on the raw responses, so a chunk failing one obligation
still scores on the others. Judged pass only if every core page in the chunk
satisfied it.

| Obligation | Success |
| --- | --- |
| **hazard judgement and attachment** | **8 / 8** |
| numbered action completeness | 7 / 8 |
| declination consistency (nothing both cited and declined) | 7 / 8 |
| coverage status consistency | 6 / 8 |
| value honesty (did not decline a value-bearing segment) | 5 / 8 |
| **exhaustive accounting** | **2 / 8** |

"Judgement stable, bookkeeping unstable" holds across eight chunks, and more
sharply than the three single-chunk calls suggested: hazard judgement is perfect
at 8 of 8, including attachment, while exhaustive accounting is 2 of 8. The
middle rows matter too - value honesty at 5 of 8 is a bookkeeping failure with
safety consequences, since declining a segment that states a value is an active
false statement about a real measurement.

### Merge and assembly

**Merge not attempted:** only 2 of 8 chunks validated, and the merge requires
every required chunk. No `ExperimentProtocol` was assembled. Even had all eight
passed, chunk 1's four unaccounted segments force
`analysis_incomplete` on pages 5, 6 and 8, which the merge refuses.

### What "canonical validation passed" means at this baseline

Recorded explicitly, because the phrase changed meaning in `4a9f130` and a
milestone stated without the qualifier would be misleading.

A chunk passing canonical validation at this baseline means: source identity,
page-local handles, contiguous evidence, exact evidence reconstruction,
identifier format, claim targeting and attachment position, numbered-action
completeness, and declination consistency all hold. It does **not** mean the
page ledger is complete. An omitted segment is recorded in
`unaccounted_segment_ids` and forces that page to `analysis_incomplete`, which
still refuses the whole-document merge. So a chunk can pass while the document
cannot be approved, and chunk 1 is exactly that case.

`unaccounted_segment_ids` was verified to reach both the stored analysis payload
and the `review()` response. One small gap: `review()` exposes a
`declined_segment_count` summary but no equivalent count for unaccounted
segments, so a reviewer must read the per-page list. Recorded, not fixed here.

### Measured cost

| | ANKOM, 40 pages, 8 calls | 20 protocols of this size |
| --- | --- | --- |
| wall-clock latency | **104.1 s** (mean 13.0 s per chunk) | 34.7 min |
| calls | 8 | 160 |
| request bytes | 35,298 | 705,960 |
| response bytes | 50,774 | 1,015,480 |
| prompt tokens | 29,882 | 597,640 |
| completion tokens | 14,575 | 291,500 |
| **total tokens** | **44,457** | **889,140** |

This is one pass with no retries. At the current 2-of-8 pass rate a document
would need repeated passes to be admitted, so the figures above are a floor
rather than an estimate of what registering a protocol actually costs today.

## 4s. Page status becomes a server derivation

The provider was declaring a segment disposition for every segment *and* a page
status. The status is a set operation over those dispositions, so the contract
asked for the same fact twice and the two answers could disagree. Three of the
eight STEP 9 failures were that disagreement.

Computability, confirmed before changing anything:

```
complete            = every substantive segment cited or declined, >= 1 item cited
no_relevant_claims  = every substantive segment declined, 0 items cited
analysis_incomplete = some substantive segment neither cited nor declined
```

All three are set operations over `(substantive, cited, declined)`. One thing is
**not** derivable: a model that closed the ledger but does not believe it read
the page properly. No set yields that, so `analysis_incomplete` survives as a
provider **boolean self-report** rather than as a status value, and it can only
make the derived outcome stricter, never looser. That separation is the point:
the mechanical fact is computed, the introspective one is asked for.

`status` is gone from the response schema, replaced by `analysis_incomplete`.
The two status/item-count consistency clauses in the validator are deleted as
dead code, because neither sentence they refused is sayable any more.
`CLAIM_SCHEMA_VERSION` stays 6: a field was swapped, and no prior response
survives the change either way.

A second contradiction went with it. A segment both cited and declined was
refused; now the citation wins and the redundant declination is dropped. The
citation is positive evidence, validated in full; declining the same segment
adds nothing. A segment that is *only* declined is still held to every rule,
including the unit cross-check.

### Effect, measured by replaying the eight STEP 9 responses

The same captured bytes, re-interpreted under the new contract (the `status`
field's only surviving meaning is the self-report):

| Chunk | STEP 9 | STEP 10, same response |
| --- | --- | --- |
| 0 | `coverage_mismatch` p3 | **passed** |
| 1 | passed | passed |
| 2 | `declined_segment_states_a_value` p13 | unchanged |
| 3 | `coverage_mismatch` p18 | `declined_segment_states_a_value` p19 |
| 4 | passed | passed |
| 5 | `declined_segment_states_a_value` p26 | unchanged |
| 6 | `numbered_action_missing` p32 | unchanged |
| 7 | `segment_claimed_and_declined` p37 | **passed** |

**Chunks passing canonical validation: 2 of 8 → 4 of 8.**

All three duplication failures are eliminated by construction. Chunk 3 still
fails, but on a different and genuine fault that the status contradiction had
been masking: it declined a segment on page 19 that states a value. So the
failure *class* is gone from all three sites, and one site had a second,
independent fault underneath it.

The three remaining failures are the ones the design intends to catch: two false
statements about measured values, and one numbered action not claimed.

## 4t. Repeated boilerplate: identifiable, and not safe to exclude

Six of the thirteen unaccounted segments were running footers or equipment
blocks, and ANKOM's footer appears on 40 pages, so repetition looked like a way
to exclude them from the substantive set on a structural fact rather than a
judgement about meaning.

Measured, masking digits so a page number does not make each footer unique:

| Threshold | ANKOM shapes | of those, content-bearing | in-gel shapes | content-bearing |
| --- | --- | --- | --- | --- |
| on >= 2 pages | 18 | **17** | 5 | **4** |
| on >= 3 pages | 4 | **3** | 1 | 0 |
| on > half the pages | 1 | 0 | 0 | 0 |

**Not adopted.** Real protocol content repeats, routinely:

- `# Flush procedure: Fill the dispenser with # ml of hot water.` - a numbered
  step with a quantity, on 3 pages
- `# Wash the gel piece with # µL acetonitrile # rpm, #°C, #:#:#` - a numbered
  step with three values, on 2 pages
- `Sartorius Practum NAME Analytical balance TYPE ... SKU` - an equipment block,
  on 3 pages
- **`Safety information HOT! Be careful!` - on 2 pages**

That last one settles it. A repetition rule at the only threshold that catches
in-gel's footer would exclude hazard text from the ledger. The justification
changed from "this looks meaningless" to "this repeats", and the failure mode did
not change at all: real execution content, including a warning, silently leaves
the accounting. That is the trap rejected in §4p wearing different clothes.

The threshold that is clean on ANKOM - more than half the pages - misses in-gel's
footer entirely, because segmentation merges the footer with adjacent content on
most of its pages. Which is the second reason: `#:#:# protocols.io | ...` is a
single segment holding both a footer *and* a real timer value, so even a perfect
footer detector would exclude a segment carrying a duration. No segment-level
boilerplate exclusion is safe while segmentation can merge boilerplate with
content.

Repetition remains a legitimate *diagnostic* - it would tell a reviewer which
segments look like running furniture - but it is not admissible as an
exclusion, and none was implemented.

## 4u. Two new documents: not measured, and why

The task named two further protocols for offline structural validation:
`intracellularmetaboliteextraction.pdf` (34 pages, 5 numbered labels, 0.1 per
page) and `usingdynamicheadspacecollections.pdf` (16 pages, 57 labels, 3.6 per
page). **Neither file is present on this machine.**

The paths given were literal `/path/to/...` placeholders. A search of the whole
filesystem by name and by content - every PDF on the box, hashed, with page
counts - found only the two known sources plus an unrelated system document. No
mount point, home directory or scratch location holds them.

Every question asked about them requires the bytes: the dual-extraction
cross-check, per-page segment distribution, the degradation detector, the
proportion of segments outside numbered steps, and the audit's risk split. None
of it can be derived from the page and label counts supplied, and none of it is
reported here rather than estimated.

The 0.1-labels-per-page document is the interesting case and the question stands
unanswered: with five numbered labels across 34 pages,
`numbered_action_missing` enforces almost nothing, so nearly the whole document
would rest on per-segment accounting and the document-level claim path - the two
mechanisms with the weakest measured reliability. That is a prediction, not a
measurement, and it is recorded as such.

## 4v. protocols.io API as an instrument: partially usable

Assessed as a measuring instrument only.

**Access.** `GET /api/v4/protocols/{uri}` requires `Authorization: Bearer
<access_token>`; unauthenticated it returns status 1218, "Authorization token is
not correct". Two tiers exist: a client access token, which reads all public
data, and OAuth for user-owned content. **No token is configured in this
environment**, so the response shape could not be verified empirically and
everything below about field structure comes from documentation.

**Structure.** A protocol carries `steps[]`, each step carrying `components[]`,
and each component is `{id, guid, order_id, type_id, title, source}` where
`source` is described as "variative object of component, can be determined by
type_id". So typed components do exist, keyed by a numeric type id, with
observed ids 1, 3, 4, 6, 7, 8, 9, 13, 15 and 17-26. The public schema
repository does not map ids to meanings. The editor exposes components for
citations, amounts, equipment, reagents by supplier, durations and safety
warnings, which is consistent with that id set.

**A useful corroboration.** The block labels we have been treating as
unnumbered prose - `Note`, `Safety information`, `Expected result`, and the
`NAME / TYPE / BRAND / SKU` equipment blocks - are almost certainly the PDF
export rendering those typed components. ANKOM page 30's hazard block being
labelled `Safety information` is then not incidental: it is a safety component
the author filled in. That is what makes the comparison worth building.

**Comparable to our claims:** numbered step count and order against action
claims; reagents and materials with supplier and catalogue number against
material claims; amounts with units against quantity and concentration claims;
durations against duration claims, where the `HH:MM:SS` literals we now parse
are the editor's duration component; equipment against equipment claims; safety
components against `warning_hazard` claims; and step nesting against our
step-attached versus document-level distinction.

**Not comparable:** evidence spans and segment identity have no counterpart, and
neither does page-level accounting.

**The asymmetry that decides how it can be used.** A component the author filled
in is ground truth for **recall**: if the API has a safety component on a step
and we produced no hazard claim there, we missed it, and that is dispositive.
The converse does not hold. An author who typed a warning into the step's prose
rather than into the safety component leaves the API silent while the PDF still
says it, so API absence is **not** ground truth for precision - it would score
a correct extraction as a false positive. The API therefore measures what the
author declared, not what the protocol contains.

Used within that asymmetry it is the first real accuracy instrument available:
a per-category recall figure against author-declared facts, on the same
documents, with no provider call needed to produce the reference side.

**Licensing.** Both documents state Creative Commons Attribution on their first
page, so the content is CC-BY and reuse requires attribution. Rate limits are
not published in any source reachable here, and the unauthenticated error
response exposes no rate-limit headers. Both need confirming against the
authenticated documentation before any comparison harness is built.

## 4w. The value-honesty rule was stated, but not as the server enforces it

Two of the four remaining whole-document failures were
`declined_segment_states_a_value`. The rule **was** in the prompt, so this is
not the `section_id` defect repeating - the sentence existed:

> Declining a segment is a statement on the record that it holds no claim, not a
> way to skip it, so do not decline a segment that states a measured value.

It was stated **semantically** and enforced **syntactically**. The server's test
is a digit immediately followed by one of a closed set of fourteen units,
case-insensitive, excluding a digit that continues a hyphenated code. "States a
measured value" is a different question, and a model answering it honestly gets
a different answer.

The four segments that failed show the divergence exactly:

| Chunk | Page | Matched | Segment |
| --- | --- | --- | --- |
| 3 | 19 | `2 h` | `Note ... more than 2 h between procedures` |
| 3 | 19 | `2 h` | `Note ... within 2 h of the previous procedure` |
| 5 | 26 | `2 h` | the same flush Note, repeated |
| 2 | 13 | `20 g` | `20 g Na2SO3 4.0 mL alpha-amylase` + running footer |

Three of the four are a **threshold inside a Note** - a condition on when to
flush, not a value an operator is asked to produce. Declining them is a
defensible reading of "measured value" and a violation of the server's test. The
fourth is a reagent line merged with the page footer, and its values repeat a
materials list claimed earlier, so it reads as already-claimed furniture.

Nothing about the enforcement was relaxed. The prompt and the schema now state
the test the server actually applies: the unit set verbatim, that the shape
decides and not the meaning, and that a threshold, an interval, a container or
pack size, and a repeat of a value already claimed elsewhere all count. The
exemptions are named too, since a model cannot infer them: a digit continuing a
hyphenated code, and a digit with no unit.

### Why the STEP 7 parity audit passed this

Two independent reasons, and the second is the more serious:

1. The audit's rule list was **hand-written**. It enumerated identifier
   patterns, the positional attachment rule and the accounting count check -
   the rules someone thought to list. Nothing derived the list of enforced
   rules from the code, so the audit could not detect an omission in its own
   enumeration.
2. It checked that a phrase was **present**, not that the phrase said what the
   server enforces. Even a complete list would have passed this rule, because
   the sentence was there. Presence is not parity.

Both are fixed. `tests/test_rule_parity.py` now walks the validator's AST and
collects every `reason_code` it can raise - fifteen - and requires each to
appear in a table declaring where the provider is told about it: PROMPT with the
exact phrases, SCHEMA where the response shape makes it unrepresentable, or
SERVER for a transport fault no instruction could avoid. A new rule cannot land
without an entry and a deleted one cannot leave a stale entry. Separately, every
unit in the server's set must appear in both the prompt and the schema
description, so extending the set without telling the model fails the suite.

Both directions were mutation-checked rather than assumed: renaming an enforced
code fails `test_every_enforced_reason_code_is_accounted_for`, and adding a unit
to the server set fails the prompt and schema unit checks. Phrase matching is
whitespace-normalized, because a prompt reflow was making a stated rule look
deleted.

### Effect on the stored responses: none, and that is the honest answer

Replaying the eight captured responses under the fixed contract gives **4/8,
unchanged**. A prompt and schema fix cannot retroactively repair a response
produced under the old prompt: the same bytes decline the same segments, so
chunks 2, 3 and 5 fail identically. Anyone reporting an improvement here would
be reporting one that did not happen.

What can be measured offline is whether the clarified rule is **satisfiable** -
the real risk, since a rule stated more precisely is worthless if compliance is
impossible. Repairing the three chunks the way the prompt now instructs, citing
each forced segment instead of declining it and attaching it by position, all
three pass:

```
chunk 2: passed (repaired 1 forced declination)
chunk 3: passed (repaired 2 forced declinations)
chunk 5: passed (repaired 1 forced declination)
```

The attachment rule accommodates them without a special case: on page 19 the two
Notes sit at offsets [101,456) and [456,698), inside step 20's block [0,698), so
they attach to step 20's action claim. This is a **constructed repair, not a
model response** - it establishes that 7/8 is reachable and that the positional
path has somewhere to put these claims. It is not a measurement of what a
provider will do.

## 4x. Cardinality headroom, and one cap that can bind

The per-page limits are `MAX_EVIDENCE_ITEM_REFS_PER_PAGE = 256` and
`_MAX_EVIDENCE_SEGMENTS_PER_SPAN = 256`. Measured over the whole-document run,
ANKOM's busiest page carried **23 evidence items** and its densest page **18
segments** - about eleven times under the limit.

For a 254-segment document the per-page caps cannot bind at all: even if every
segment in the document fell on one page it would stay under 256. That holds
whatever the distribution, so the document-level path carrying most of a
near-unnumbered document is not at risk from these two limits.

`MAX_PAGE_COVERAGE_RECORDS = 32` is the cap that can bind. It allows 32 core
pages per chunk, so a 34-page document cannot be analysed as a single chunk. It
is not a constraint on chunked operation - the eight-chunk plan uses three to
five core pages per chunk - but a whole-document single call would be refused on
cardinality before any claim was read.

## 4y. The two new documents, and the three calls not spent

`intracellularmetaboliteextraction.pdf` and
`usingdynamicheadspacecollections.pdf` are still not on this machine. Checked
again this session: by name across the filesystem, and by listing every PDF on
the box, which returns the two known protocol sources plus one unrelated system
document.

So the offline scoring of the new documents is skipped, as instructed. The three
authorized provider calls are **not spent**. They were authorized for a specific
target - the three chunks of the intracellular document with the highest
proportion of segments outside numbered steps - and that target cannot be
selected without the file. Spending them on ANKOM instead would consume an
approved budget on something that was not approved and would answer none of the
questions asked, so the budget stands unused at 0 of 3.

The externally supplied figures are recorded here as the reason the document
matters, not as measurements taken here:

| Document | Labels/page | Segments | Segments/page | Off-step | Degradation |
| --- | --- | --- | --- | --- | --- |
| headspace | 3.6 | 103 | 6.4 | 18% | 0 pages |
| ANKOM | 1.7 | 174 | 4.3 | 20% | 1 page (p17) |
| intracellular | 0.1 | 254 | 7.5 | **61%** | 1 page (p10) |

Extraction and segmentation pass on all three, so the mechanical layer does not
break. What is untested is the consequence of 61% off-step: with five numbered
labels over 34 pages, `numbered_action_missing` enforces an action claim only
where a label exists, which on this document is five places. Everywhere else the
only obligations left are per-segment accounting - the weakest measured
obligation at 2/8 in the whole-document run - and the document-level claim path.
That is a prediction from the structure, and it is not reported as a result.

## 4z. The cross-check was blind, and the blindness was mine

Reproduced before changing anything. Every figure below is measured here.

| Document | Pages | U+FFFE | Verdict before | Verdict after |
| --- | --- | --- | --- | --- |
| ANKOM | 40 | 9 | verified | **mismatch**, pages 9, 17, 25, 33, 35-39 |
| intracellular | 34 | 12 | verified | **mismatch**, pages 4, 10, 18, 19, 21, 33, 34 |
| headspace | 16 | 0 | verified | verified |
| in-gel | 9 | 0 | verified | verified |

Every occurrence is U+FFFE, a noncharacter, category `Cn`. The cause is in
`canonical_text_census`, and I put it there: an earlier step added `Cn` to the
dropped categories to silence this exact character, on the reasoning that one
engine emits noncharacters as padding where another emits nothing. That
reasoning was wrong. U+FFFE turns up here **in place of** a document character,
not as padding beside one, and because the census also drops every hyphen
variant, `alpha-amylase` and `alpha\ufffeamylase` produced identical censuses.
Two engines that genuinely disagree were reported as agreeing.

The affected text is not incidental. On ANKOM page 9 it is inside numbered step
11's evidence segment, in a reagent dosing sentence: `Add 8.0 mL of
alpha\ufffeamylase and enough tap water to fill the dispenser`. The same
compound word appears correctly earlier in the same segment, so this is
positional, not a font-wide mapping failure.

### What the character is taken to mean

A noncharacter is permanently reserved and can never be assigned; a private-use
code point has no meaning outside the font that defines it. Either one in
extracted text means the extractor could not map a glyph, so **the character at
that position is unknown**. It is refused, never substituted. Three measured
reasons, not one assumption:

- The comparator does not recover it either. Where we emit
  `alpha\ufffeamylase`, `pdftotext` emits `alphaamylase` - the hyphen deleted
  outright. Neither engine produces the word.
- The comparator is demonstrably wrong elsewhere. In a reference DOI we emit
  `978-1\ufffe4939` where it emits `978-14939`, which is not the DOI.
- The right character is not the same everywhere. `alpha-amylase` takes a
  hyphen; `Liquid Chromatography-Mass Spectrometry`, where U+FFFE also appears,
  conventionally takes an en dash. Any single substitution is wrong somewhere.

The specific harm a guess would do belongs to this design: the affected text is
quoted as canonical `source_text` and then confirmed by exact-evidence
validation, so the pipeline would certify a character we invented. That is the
one thing this architecture exists to prevent.

`Cn` is now kept in the census beside `Co`. Separately, unmapped code points are
decided **before the comparator is consulted at all**, because they are a
property of our own extraction rather than of any disagreement. Deciding them
only by comparison would leave them undetected wherever `pdftotext` is not
installed, and `comparator_unavailable` is a gate a person may acknowledge - so
genuinely unmapped text could have been waved through. `MISMATCH` is refused at
admission and its readiness gate is not acknowledgeable.

The two mechanisms agree exactly: the pages carrying unmapped code points are
the pages the census now finds divergent, and the divergence is **only** the
noncharacter - the comparator contributes no extra characters on any page. So
the blindness hid this one class and not a broader corruption.

Consequence, stated plainly: **ANKOM is no longer admissible.** Our primary
document fails its own cross-check on nine pages. That is the correct outcome
for text we know contains unknown characters, and it is why this was fixed
before anything else was measured.

## 5a. The numbered-action trigger fires on figure captions

Reproduced. `_numbered_action_matches` finds 36 labels in the intracellular
document, and not one is an execution step:

- **23** are figure references - 22 captions of the form `Figure N.` plus one
  mid-sentence `as shown in Figure 22.`
- **1** is a reference's volume and page, `17(1), 146.`
- **12** are section numbers and a table of contents
- **0** are numbered execution steps

Ten of the twelve chunks contain at least one label, so the obligation can fire
on 10/12. That is my figure; the brief said 9/12. The difference is what is
being counted: 10 chunks are *exposed*, meaning a response lacking a matching
action claim is refused. How many are actually refused cannot be established
without a response for each, and only one call was authorized.

### One authorized call, and the model did not invent the action

Chunk 8 was chosen: core pages 25-28, four `Figure N.` captions plus the
`Figure 22` cross-reference, the most caption labels of any chunk whose pages
carry no unmapped code points. Sending it required clearing the verification
flag on a local copy, since the document is now refused at admission; the
harness asserts every page it sends is free of unmapped code points first, and
nothing it does admits the document.

The obligation demanded action claims labelled 18, 19, 20, 22 and 21.

| Observed | |
| --- | --- |
| Claims returned | 11 - 10 action, 1 warning_hazard |
| Action claims carrying a demanded label | **0 of 5** |
| Labels actually emitted | page 27: `4.7`, null; page 28: null x 8 |
| Document-level vs step-attached | 10 vs 1 |
| Declined segments | 0 |
| Pages self-reported incomplete | none |
| Canonical validation | rejected, `numbered_action_missing`, page 25 |
| Latency / response | 9.2 s / 5057 bytes, 4443 prompt + 1586 completion tokens |

The model produced actions from the prose and attached almost all of them at
document level, which is what a 61%-off-step document should produce. It did
**not** manufacture an action out of `Figure 18. Example of incorrect
identification of closely eluting peaks`. Pages 25 and 26 got no action claim at
all, and the chunk was refused on page 25.

So the fault is the server rule, not the model. Two further observations: a
`warning_hazard` claim was produced and attached to a step, on a document said
to carry no safety blocks; and the per-segment accounting outcome is **not
measured**, because validation aborts at the page-25 label check before
accounting runs.

## 5b. Four directions for the trigger, none implemented

Each is judged against the guarantee the rule exists for - a numbered execution
step is never silently dropped - and against parity, overlap and measurability.

### D1. Dispose of every label, rather than assert an action for it

Every label the server derives must be answered: an action claim carrying that
label, or an explicit per-page declaration that the label is not an execution
step, which the server stores and counts.

- **Guarantee**: preserved, arguably strengthened. A label cannot be dropped
  silently; it must be positively judged, on the record.
- **Overlap**: complementary, neither subsumes the other. Segment accounting
  asks whether the *text* was addressed; this asks whether the *label* was
  judged. In the call above the caption text was partly cited while the label
  question went unanswered, and the converse - a label declared a non-step whose
  segment is left unaccounted - is still caught by accounting.
- **Parity**: expressible, and this is the work. The prompt must state the label
  shape the server derives, including the filter that skips a label followed by
  a number or a unit. Saying "numbered steps" instead would repeat the STEP 11
  error exactly.
- **Measurability**: satisfied by construction - declarations are stored per
  page and countable.
- **Cost**: a schema field and a prompt paragraph, and it asks the provider to
  judge 36 labels on a document with no steps.

### D2. Drop the inline matcher, keeping only the line-anchored one

`_NUMBERED_SOURCE_LINE` is already anchored at line start;
`_INLINE_NUMBERED_SOURCE` matches a number mid-line. Measured, per matcher:

| Document | Total labels | Line-anchored | Inline-only |
| --- | --- | --- | --- |
| ANKOM | 67 | 67 | **0** |
| in-gel | 25 | 25 | **0** |
| headspace | 61 | 61 | **0** |
| intracellular | 36 | 12 | **24** |

The inline matcher produces every one of the 24 false triggers on intracellular
- all 23 figure references and the reference page number - and contributes
nothing on the other three documents.

- **Guarantee**: preserved for every label that begins its own line, which is
  100% of labels on all three properly numbered documents. It is weakened only
  for a numbered step appearing mid-line, and no such step exists in any of the
  four documents measured.
- **Overlap**: none - it changes the trigger, not the obligation. Caption text
  remains content to account for, which is the correct division: a caption is
  something to account for, not something to execute.
- **Parity**: substantially easier than today. One anchored regex is statable
  exactly; the inline alternative is what makes the present rule hard to state
  without paraphrasing it.
- **Measurability**: this is the condition of adopting it. The narrowing must
  record how many labels it dropped per document, or it is a silent blocklist.
  Because it changes the trigger rather than keeping an exclusion list, the
  instrumentation is to report both counts, so a reviewer sees "24 labels were
  mid-line and were not treated as steps".
- **Cost, measured**: 15 tests fail if the matcher is removed. Their fixtures
  put an entire page on one line, so their numbered steps are inline only as an
  artifact of the old single-line fixture writer. The multi-line writer already
  exists, so this is fixture migration - and it must be migration, never
  loosening the assertions.

### D3. Require the label sequence to be monotonic

Rejected. **Parity fails structurally**: monotonicity across a document is a
global property, and a chunk sees three to five pages, so the provider cannot
compute the predicate the server would enforce from what it is given. It also
drops real steps in any protocol whose numbering restarts per section, and
counting the drops would not rescue that.

### D4. Do nothing to the trigger and rely on accounting alone

Rejected, but worth stating so the choice is explicit. Accounting asks only that
something be said about each segment; it cannot tell a numbered step that was
skipped from prose that was declined. ANKOM's whole-document run caught a
genuine `numbered_action_missing` on page 32, which accounting alone would not
have caught. The guarantee is real and something must carry it.

**D2 then D1** is the ordering the measurements support: D2 removes 24 of the 24
false triggers at no measured cost on any real document, and D1 then answers
what remains, including intracellular's 12 section and contents numbers, which
are line-anchored and survive D2.

## 5c. The unmapped-glyph gate splits in two

STEP 12 refused every unmapped code point on the ground that what it stood for
was not recoverable. That premise was wrong, and it was wrong because I only
looked at the layout. A PDF declares what its glyphs mean, in each font's
ToUnicode map, and that declaration is the document speaking about its own
text.

Verified here, from the font itself rather than from any extractor: ANKOM
page 9 carries a Type3 subset font whose ToUnicode contains `<B6> <002D>`. The
engine that applies ToUnicode reads `alpha-amylase`. **U+FFFE is not in the
document; it is what pdfium emits where it failed to map that glyph.**

So the gate has two classes.

**Class 1 - the document declares the character and an engine reads it.**
Reading it is not repair. It is reading the source, and "the server owns the
authority over its evidence" is exactly this case. Admitted.

**Class 2 - no engine reads a character the document declares.** The document
says nothing about the position. Refused, and here the earlier decision holds.

### Measured, all four sources

| Document | Unmapped | Class 1 | Class 2 | Alignment failed | Verdict |
| --- | --- | --- | --- | --- | --- |
| ANKOM | 9 | **9** | 0 | 0 | **verified** |
| intracellular | 12 | 7 | 3 | 2 | **mismatch**, pages 10, 18, 33 |
| headspace | 0 | - | - | - | verified |
| in-gel | 0 | - | - | - | verified |

The three Class 2 cases are all positions where the second engine also emits a
private-use placeholder, U+E088: a part number, the compound name
`fructose-1,6-bisphosphate`, and a DOI. The two alignment failures are both
DOIs on page 33, where no two engines can be lined up around the position.

**ANKOM returns to admissible**, and page 9 now reads `Add 8.0 mL of
alpha-amylase`. Intracellular stays refused, correctly: it contains five
positions the document does not settle. Resolution is applied per page and
all-or-nothing, so of the 7 declared positions in that document only 5 are on
pages that fully resolve; the other 2 share a page with a Class 2 position and
are refused with it.

### The rules the resolution obeys

- The interpretation comes **only** from the document's ToUnicode declaration.
  A character an engine reports is accepted only if the PDF declares it. There
  is no majority vote between placeholders, no "it looks like a hyphen", no
  inference from surrounding words. Two engines agreeing on an undeclared
  character is still refused.
- Two engines reporting **different real characters** is a genuine conflict and
  is refused.
- **Deletion says nothing.** An engine that joins the words around the position
  neither resolves it nor conflicts with anything. This matters because the
  comparison engine deletes at every one of these positions.
- Every resolved position is recorded in `glyph_resolutions` with its page,
  offset, the code point that was there, the character read, the engine that
  read it, and that the document declared it. A position that cannot be
  recorded this way is not admitted.
- The invariant is checkable and tested: **admitted page text contains no
  unmapped code point at all.** Either every position resolves from the
  document, or the source is refused.

### Why the third engine, and what it is not for

The failure modes had to differ for the check to mean anything: pdfium
substitutes U+FFFE, the ToUnicode reader substitutes a private-use code point,
the comparison engine deletes and joins. With only the first and third,
substitution and deletion cancel inside the census -- which is precisely why
all 21 positions passed.

But the third engine is **not** a census comparator, and measuring that was
worth the time. Against the primary it reports divergence on **68 of the 99
pages** across the four sources, including in-gel and headspace, which contain
no unmapped glyph at all. On in-gel page 2 the difference is four `(`
characters it silently drops, with nothing offered in return, while the
comparison engine matches the primary exactly. Promoting it to a comparator
would refuse everything for its own defects. It is used only to say which
character stands at one already-identified position, and only the document's
declaration makes that answer admissible.

So the census itself now compares real characters only, and unmapped positions
are decided one at a time. That reads like a reversal of the STEP 12 fix and it
is not: what was wrong before was that dropping them left the position handled
by **nothing**. It is now handled by a stricter mechanism than the census could
ever be.

`EVIDENCE_SEGMENT_VERSION` moves 4 to 5. Resolving a glyph changes page text,
so it changes `page_text_sha256` and every canonical segment id derived from
it. Any analysis stored against version 4 is invalidated, which is right: it
was computed over text containing a character the document had declared and we
had not read.

## 5d. The offline scorer was manufacturing execution steps

The scorer asserts an ACTION claim for every numbered line it matches. On
intracellular page 4 that produced six action claims from section headings and
a table of contents -- `1 Intracellular metabolite analysis provides a
snapshot...`, `2 This protocol is organized into the following key steps:` --
and validation accepted all six. The real provider, given the same kind of
page, produced no such claim. **The instrument was less honest than the thing
it was measuring, and the validator could not tell them apart.** The product's
most dangerous failure mode, inventing an execution step, was happening inside
the measuring tool.

The scorer is deliberately **not** made cleverer. A fixture that judged which
lines were steps would be a model, and there would be no measuring standard
left. Instead it reports when it is outside its own scope and declines to
score.

The scope test is arithmetic on the labels, never a judgement about their text:
a numbered step sequence is an ordered enumeration, so in reading order the
labels must strictly increase. A repeat or a descent means at least one matched
line is not a step in that sequence, and the fixture cannot tell which.

| Document | Fixture action labels | Strictly increasing | Duplicates | Descents | Verdict |
| --- | --- | --- | --- | --- | --- |
| ANKOM | 67 | yes | 0 | 0 | scored |
| in-gel | 25 | yes | 0 | 0 | scored |
| headspace | 62 | yes | 0 | 0 | scored |
| intracellular | 12 | **no** | **7** | **4** | **fixture out of scope** |

ANKOM, in-gel and headspace are all clean, so **the STEP 9 and STEP 11 numbers
stand.** Had any of them been non-zero, every figure quoted from them would
have had to be withdrawn.

Out of scope means no score is emitted at all -- not a score with a caveat
beside it. A number published next to its own disclaimer gets quoted without
it.

This is a scope test on a test double, not a server rule. The same
monotonicity idea was rejected for the server as D3, where it fails parity: a
provider sees three to five pages and cannot compute a document-global
predicate. The fixture sees the whole document and enforces nothing on anyone.

## 5e. D2 implemented: the trigger is line-anchored only

`_INLINE_NUMBERED_SOURCE` is no longer part of the numbered-action trigger. A
numbered step begins its own line; a number inside a sentence is a
cross-reference or a citation.

| Document | Labels now | Dropped as mid-line | Chunks exposed to the obligation |
| --- | --- | --- | --- |
| ANKOM | 67 | **0** | 8/8 (unchanged) |
| in-gel | 25 | **0** | 3/3 (unchanged) |
| headspace | 61 | **0** | 4/5 (unchanged) |
| intracellular | 9 | **24** | **10/12 to 4/12** |

**D2 alone is not the end, and the remaining exposure is the proof.** Nine
line-anchored labels survive on four chunks, and none is an execution step:

```
page  4: 1, 2, 3    section headings and a contents list
page 13: 4          section heading
page 18: 1          section heading
page 19: 2          section heading
page 30: 5, 1, 2    section heading and a contents list
```

The guarantee is intact where it has actually earned its keep: page 32 of
ANKOM, where the whole-document run caught a genuine omission, still carries
line-anchored labels and still demands its action claims.

The narrowing is instrumented rather than silent. `mid_line_numbered_labels`
returns, per page, the labels the trigger no longer treats as steps, so a
reviewer can see how much a document depends on the narrowing instead of
taking it on trust. A count nobody can read is a blocklist.

Fifteen tests failed, and every one was migrated rather than loosened. Their
fixtures wrote an entire page as a single line, so their numbered steps sat
mid-sentence and matched only because of the pattern being removed. Several had
been **written** with newlines in them that the fixture writer was silently
replacing with spaces -- `"Preparation\n1. Add buffer."` became one line. The
writer now emits the lines the fixture asked for, and a page with no newline is
written exactly as before. One test that asserted the old collapsing behaviour
as a feature was rewritten to record why it was wrong.

## 5f. D1 designed, not implemented

Every label the server derives must be **answered**, not necessarily claimed:
either an action claim carrying that label, or an explicit per-page list of
labels declared not to be execution steps, which the server stores and counts.

**Guarantee.** Preserved and strengthened. A numbered line cannot be dropped
silently; it must be positively judged, on the record, and the judgement is
countable per document.

**Overlap with all-segment accounting.** Complementary; neither subsumes the
other. Accounting asks whether the *text* was addressed, D1 asks whether the
*label* was judged. The authorized call showed both gaps are real: the caption
text was partly cited by prose claims while the label question went unanswered.
The converse -- a label declared a non-step whose segment nobody accounts for
-- is still caught by accounting.

**Parity, which is where the work is.** The prompt must state the trigger as
the server computes it, or this repeats the STEP 11 error of describing a rule
semantically and enforcing it syntactically. The server's rule, in full:

```
A label is a number of one to three digits, starting at 1 to 9, that begins
its own line, optionally followed by "." or ")", then at least one space or
tab, then a non-space token. The label is not a step if that following token
is itself a number, or is one of the fourteen measurement units.
```

Both halves must be in the prompt: writing "numbered steps" would leave the
model unable to enumerate the same set, and omitting the number/unit filter
would make it enumerate a larger one. The schema carries the same statement on
the new field's description, and the parity audit gains a row binding the
enforced trigger to those phrases, so the regex and the prompt cannot drift.

**Measurability.** Satisfied by construction: declarations are stored per page,
so "how often did a label get declared a non-step" always has an answer.

**Cost.** A schema field and a prompt paragraph, and it asks the provider to
judge nine labels on a document with no steps -- down from thirty-six before
D2, which is why D2 came first.

Not implemented, awaiting approval. The one remaining question worth a call is
whether a model that is *told* the trigger's exact shape will dispose of a
section heading correctly, and that is a single call on the same chunk 8.

## 5g. The hazard claim from the authorized call

Evidence validation runs before the numbered-action check, so every claim in
that response had already passed exact-evidence validation when the chunk was
refused. The `warning_hazard` claim therefore quotes a real span of the source.

Reading pages 25 to 28, the only text on them that reads as a warning is a Note
on page 27:

> Since the software tends to crash frequently, we highly recommend saving your
> progress after completing peak selection for each compound.

That is a data-loss caution about software stability. There is no chemical,
thermal or physical hazard anywhere on those four pages, which are about peak
integration in an analysis tool.

**Which span the claim actually cited was not persisted**, so this cannot be
confirmed: the harness deliberately records structure only. That is the price
of not persisting provider output, and it is worth naming rather than papering
over -- the honest statement is that the claim was substantiated as *some*
exact source text, that the only warning-like text available is that Note, and
that the identification is inference rather than measurement.

If it was that Note, the claim is a correctly-evidenced warning that is
miscategorised as a hazard. That has a consequence worth recording, though
nothing here acts on it: `declared_safety_warning_count` counts
`warning_hazard` claims, so a software-crash caution would clear the
`NO_DECLARED_SAFETY_WARNINGS` readiness gate on a document with no physical
hazard at all. The gate is not being widened or narrowed here; the observation
is logged for a step that has a measurement to act on.

## 6a. The safety gate was opened by the thing it was meant to check

Reproduced before changing anything, exactly as described:

```
provider warning present=False: count=0 gate=True  status=analysis_required
provider warning present=True : count=1 gate=False status=guidance_ready
```

One provider-produced warning took readiness from `analysis_required` to
`guidance_ready`. The gate exists to compel a human to confirm the safety
picture, and the model's own output was waiving it.

The diagnosis is that the gate was counting. A warning in the Protocol is a
warning **the provider produced**, so a non-zero count records that a model
called something a hazard - never that the document declares one. STEP 13's
authorized call showed how thin that can be: the only warning-shaped text on
the pages concerned was a note that the analysis software crashes often, with no
chemical, thermal or physical hazard anywhere.

**The fix is that the count no longer clears anything.** The gate is raised
whenever there are steps to execute, and only an audited human acknowledgement
clears it. No word list is involved and none was added: what counts as a hazard
is still the provider's judgement, and whether this Protocol may execute on that
judgement is now a person's. The claims themselves are untouched - only the
authority to open the gate moved.

Two projections keyed off the same count and are now derived from the gate:

- `hazard_review_required` was true only when the count was non-zero, so a
  Protocol with no extracted warning at all asked for **no** hazard review.
- `gates.hazard_review` reported a bespoke `not_declared` for a zero count,
  which reads as a finished gate for exactly the case that most needs a
  reviewer. It now reports `review_required` like any other count.

A third inconsistency surfaced while doing this and is fixed with it:
`development_activation_allowed` was computed from the raw readiness status,
which does not know about acknowledgements, so a Protocol whose only blocker a
reviewer had already signed off could never be activated. The catalog owns the
acknowledgement ledger, so it now reports `readiness_gates_cleared` and the
server reads it instead of re-deriving it.

The blast radius is the honest consequence: **every** Protocol now needs an
explicit safety confirmation before it can be approved or development-activated.
Twenty tests failed, all of them asserting readiness for fixtures that happened
to carry a warning, and each was updated to either assert the gate or clear it
through the audited path. None was loosened.

## 6b. Evidence handles are kept; content still is not

STEP 13 could not establish which span a hazard claim cited, because the
response had not been persisted. The cause was reading one rule too broadly.

The rule now reads explicitly:

- Persisting **what the provider wrote** stays forbidden.
- Persisting an **evidence handle** - segment id, page, and the offsets it
  resolves to - is not covered by that. A handle is a server-computed identity
  for a span of text the server already owns: a pointer into the document, not
  a sentence anybody wrote. Keeping it agrees with the server owning authority
  over its evidence rather than conflicting with it.

`ClaimSourceEvidence` already carried `evidence_segment_ids`, and
`_domain_evidence` was dropping them on the way into the domain - that single
line is why the basis became unrecoverable. `domain.SourceEvidence` now carries
them, and `reopen_evidence_span` reads the source text a stored statement's
handles point at, recomputing it from the source rather than trusting anything
saved beside it. A handle that no longer resolves fails closed, because a
handle that has stopped pointing anywhere means the source or the segmentation
changed and the stored evidence can no longer be trusted.

One consequence had to be caught rather than accepted: adding the field leaked
it into the **provider-facing** schema of the older analysis path, which would
have asked a provider to supply server-owned identities - an invitation to
invent one. It is withheld there for the same reason the extraction record is
withheld from `ProtocolMetadata`, and the schema-parity test now records that
exemption. The pinned curated-fixture schema hash is unchanged as a result.

The round trip is proven both ways: handles survive serialization and reopen the
same span, the text comes back from the **source** rather than from the stored
excerpt (a tampered excerpt does not change what is reopened), and a stale
handle, a wrong revision, or no handle at all each raise rather than guess.

## 6c. The scope check's known hole, measured and pinned

The increasing/duplicate/descent test caught the near-unnumbered document, and
it will not catch this shape: a procedure written entirely as prose, with a
reference list at the end numbered cleanly `1. 2. 3.`. The labels increase, so
the document is scored and the fake execution steps built from the bibliography
go into the score. Monotonicity detects interleaving; it cannot detect a single
clean ascending run that is not a procedure.

Measured on the four local sources, none shows that shape - labels are spread
through the body rather than confined to the tail:

| Document | Labels | Pages with labels | Page range | Label span | Tail-only |
| --- | --- | --- | --- | --- | --- |
| ANKOM | 67 | 32 | 4-39 of 40 | 1-67 | no |
| in-gel | 25 | 7 | 3-9 of 9 | 1-25 | no |
| headspace | 62 | 13 | 4-16 of 16 | 1-62 | no |
| intracellular | 12 | 5 | 4-30 of 34 | 1-5 | no |

**Not fixed, deliberately.** A rule inferred from four documents would be the
same mistake as a word list. The limitation is written into `fixture_scope`'s
docstring and, more usefully, held as an executable test: a synthetic prose
document with numbered references is asserted to be **wrongly in scope**, as
current behaviour rather than desired behaviour. A future rule that closes the
hole makes that test fail loudly instead of passing silently.

## 6d. Where each document actually stops

Measured, with the gate and resolver as they now stand.

| Document | Extraction | Page admission | Chunk analysis | Merge | Assembly |
| --- | --- | --- | --- | --- | --- |
| ANKOM | verified | 8 chunks | **stops: 4/8 valid** | not reached | not reached |
| in-gel | verified | 3 chunks | never run | not reached | not reached |
| headspace | verified | 5 chunks | never run | not reached | not reached |
| intracellular | **stops: mismatch** | refused | not reached | not reached | not reached |

- intracellular stops at page admission with `ProtocolChunkAdmissionError`,
  "Protocol source text failed independent extraction cross-check" - the five
  unmapped positions the document does not declare.
- ANKOM plans eight chunks and reached 4/8 valid in the whole-document run.
  Merge requires every chunk to validate, so it has never been attempted.
- in-gel and headspace are admissible and have never been analysed; no call has
  ever been spent on either.

### Has an ExperimentProtocol ever been assembled?

**Not from a PDF through the claim pipeline.** The live store holds exactly one
analysis revision, `candidate-a-curated-development-v1`, analysis id
`curated-...`, `analysis_schema_version: 1`, written by a
`development_fixture_materialized` event on 2026-08-30. It is the hand-curated
development fixture over `in-gel-digestion.pdf`, not pipeline output, and its
own readiness is `analysis_required` for `unresolved_ambiguity` and
`unsupported_repeat_until`.

The ANKOM protocol was registered in the same store and analysis was requested
five times, started four, and **failed every time**: once
`provider_configuration_missing`, then four times
`protocol_analysis_invalid_evidence`. There is no `protocol_analysis_ready`
event in the store.

### Is it connected to the voice execution path?

The adapter exists. `ProtocolCatalog.load_executable_fixture` builds a
`CuratedProtocolFixture` from a stored analysis's `ExperimentProtocol`, and
`CuratedProtocolSession` consumes that fixture; `curated_protocol.py` never
references `ExperimentProtocol` directly, so this adapter is the whole seam.

What is missing is not the seam but anything to put through it. The adapter
requires `entry.available_for_execution`, which requires approval or
development activation, which requires readiness to be clear - and no
pipeline-produced analysis has ever existed to clear. So the path has only ever
carried the curated fixture, and the untested span is precisely: merged
`ExperimentProtocol` -> `load_executable_fixture` -> `CuratedProtocolSession`.
## 7. One PDF taken as far as it goes

`in-gel-digestion.pdf`, offline, zero provider calls, in a temporary catalog.
The operational store was opened read-only and never written.

**This is a plumbing check, not a quality measurement.** The claim model is the
deterministic offline fixture, so the analysis fed to every later stage is
fixed and synthetic, and nothing here says anything about what a real provider
would produce. What it establishes is whether the stages are connected. The
fixture is entitled to run on this document: 25 labels, strictly increasing, no
duplicate and no descent.

| Stage | Result |
| --- | --- |
| 1 extraction | ok - 9 pages, verified, 0 glyphs to resolve |
| 2 page admission | ok - 3 chunks |
| 3 chunk analysis | ok - 3/3 validated |
| 4 whole-document merge | **ok - 86 claims, 2 markers. First time attempted.** |
| 5 ExperimentProtocol assembly | **ok - 25 steps. First time reached.** |
| 6 readiness | ok - `analysis_required` |
| 7 audited safety confirmation | ok - ledger entry, actor recorded |
| 8 development activation | **STOPS** |
| 9 load_executable_fixture | not reached (blocked by 8) |
| 10 first step guidance | not reached (blocked by 8) |

Stage 8 refuses with "Protocol readiness (analysis_required) is not ready for
development execution". The reasons are `unresolved_ambiguity` x4,
`no_declared_safety_warnings` (acknowledged at stage 7) and
`unsupported_repeat_until` x2. The last two classes are not acknowledgeable, so
no confirmation clears them.

Stages 9 and 10 were then probed **with the wall stepped around on purpose**,
building the fixture directly the way `replay_turns` does. This is a
diagnostic, not a route: it changes no rule and makes nothing executable, and
without it a plumbing fault in the last two stages would be indistinguishable
from the policy wall in front of them. Both work - the session opens on the
assembled protocol and returns a populated frame for step 1: `step_id=step-1`,
`step_label=1`, 4 parameters, 1 action. **So the plumbing is continuous from
PDF to first step guidance; the only wall is the readiness policy.**

### The wall is not something the pipeline introduced

The hand-built fixture over this same document, written on 2026-08-30, carries
`unresolved_ambiguity` and `unsupported_repeat_until` x2 - the same two
non-acknowledgeable classes. Its reason codes are not a subset of the
acknowledgeable gates either, so **it cannot reach `available_for_execution`
today any more than the pipeline result can.** Stages 8 to 10 have never been
traversable for this document by any route.

That also explains how the demo runs at all: `replay_turns` constructs a
`CuratedProtocolSession` directly and never passes through
`load_executable_fixture`. There are two ways into the session, and the gated
one has still never carried anything.

## 7a. Two plumbing defects found and fixed

**Every instruction appeared twice.** The assembly branch that routes
untargeted claims into `before_start` is documented as taking "a value or
condition stated outside every numbered step", and it was also catching action
claims. An action claim is not a value or a condition - it *is* the numbered
step, and it is assembled into `sections[].steps` further down. Measured on
this document, the catch-all received 25 action claims plus 1 duration and 1
temperature, so 25 of 27 before-start entries were duplicates of the
executable steps. Excluding actions restores the branch's own stated intent and
leaves `before_start` at 2, both genuinely outside the numbered steps: an
overnight digestion time and a temperature stated after the last step. The two
stray values still surface, which is the point of the branch.

**A stored analysis had become undecodable, and I caused it.** Adding
`SourceEvidence.evidence_segment_ids` in STEP 14 broke the store's decoder,
which required the stored field set to match the dataclass exactly. The curated
fixture written months earlier no longer loaded at all - the comparison below
could not be run until this was fixed. A field added since a payload was
written is now filled from its default, while a field with no default is still
required, so a genuinely truncated record is still refused. This is the second
time a schema-shaped change has reached further than intended in this
programme; the first was the same field leaking into the provider-facing
schema.

## 7b. Pipeline output against the hand-built fixture

Facts only. This asks whether the assembly path loses information, not whether
the offline model is any good - it is a fixture and its output is fixed.

| | Hand-built | Pipeline |
| --- | --- | --- |
| sections | 5 | 1 |
| steps | 25 | 25 |
| step labels | 1-25 | 1-25, **identical set** |
| values on steps | 1 | 122 |
| sub-actions | 3 | 25 |
| warnings (step / action) | 1 / 0 | 0 / 0 |
| before_start | 1 | 2 |
| materials | 6 | 0 |
| equipment | 1 | 0 |
| constructs | RepeatUntil 2, SourceAmbiguity 1 | SourceAmbiguity 4, RepeatUntil 2 |

**No step label is lost in either direction** - both carry exactly 1 to 25.
That is the assembly path's core obligation and it holds.

In the hand-built fixture and not in the pipeline result: 6 materials, 1
equipment item, 1 step warning, and 4 of the 5 sections. Each traces to a claim
category the offline model never emits - it produced only action,
agitation_speed, concentration, duration, quantity, repeat_condition and
temperature claims, and exactly one section marker. So these are absences at
the model, not losses in assembly.

In the pipeline result and not the hand-built fixture: 121 further values, 22
further sub-actions, and 3 further ambiguities. The values are the offline
model's per-occurrence parameter extraction, which the hand-built fixture
records once at most.

## 7c. Classification of every stop

**(가) Plumbing defect - fixed this step.**

- Action claims duplicated into `before_start`. The catch-all caught a category
  already materialised as a step.
- The store decoder refused any payload written before a field existed, which
  made the pre-existing curated analysis unreadable.

**(나) Rule working - left alone.**

- `unsupported_repeat_until` x2. The source genuinely contains repeat-until
  constructs and the P1 capability policy does not support them. Corroborated
  independently: the hand-built fixture over the same document carries the same
  two. Refusing to execute a construct the policy cannot honour is the rule
  doing its job.
- Stage 8's refusal itself, for the same reason.

**(라) Data the offline model cannot supply - reported, not fixed.**

- `unresolved_ambiguity` x4, all `ambiguous-duration-*`. Each is a step whose
  action carries two duration claims because the source states one duration
  twice, in prose and again as a timer literal: `Incubate the gel plug for
  15min at 37C ... 00:15:00`. The model cannot know the two refer to the same
  interval, and assembly is right to refuse a step with two durations. The
  hand-built fixture has one ambiguity rather than four, so a person resolved
  the redundancy; whether a provider does is unmeasured.
- Missing materials, equipment, warnings and 4 of 5 sections, as above. The
  fixture emits none of those categories.

**(다) Over-blocking - none found.** Every stop traces either to a construct
the policy declines to support, or to information the offline model does not
produce. The hand-built fixture meets the same wall, which is the strongest
available evidence that the wall is not aimed at the pipeline.

## 7d. What the same run would cost with a real provider

in-gel plans **3 chunks**, one call each, so the floor is **3 calls** if every
chunk validates first time. Merge requires all three, so one unrecovered chunk
failure ends the run.

The only measured per-chunk first-attempt rate is ANKOM's **4 of 8** under the
current contract. ANKOM is a larger and denser document, so this is an
indicative figure from a different source rather than a rate for in-gel, and it
is the only measurement that exists. Taking p = 0.5 per attempt:

| Retry budget per chunk | Calls used | P(all 3 chunks validate) | Expected calls |
| --- | --- | --- | --- |
| 0 (harness default today) | 3 | 12.5% | 3 |
| 1 | 3-6 | 42% | ~4.5 |
| 2 | 3-9 | 67% | ~5.3 |
| 3 | 3-12 | 82% | ~5.7 |

Two calls of authorized budget remain, which is below the floor of 3. Replaying
stored responses stays free; only new calls cost.

## 8. A reviewer can settle an ambiguity

The system had acknowledgement but no resolution, so it stopped permanently on
something a person settles in seconds. Acknowledging a readiness gate is the
wrong instrument: `unresolved_ambiguity` appears once per ambiguity, and
clearing the reason code would clear all four at once, including ones nobody
had looked at.

A finding is now recorded **per ambiguity**, in the append-only ledger:

- **`single_statement_is_authoritative`** - the two statements say the same
  thing and this is the one to trust. This is the only finding that clears
  anything.
- **`statements_are_distinct`** - they really are different and this cannot be
  settled. That is a finding, not a resolution; the Protocol stays blocked.

Each finding stores the actor, their role, the store's timestamp, the ambiguity
id, its step and action, the source page, the evidence handles the reviewer
read, and their comment. The handles are **resolved against the source before
the finding is accepted**, so a decision cannot rest on a span that does not
exist - which is what STEP 14's handle work was for.

What it does not do is as important:

- **The source is never edited, no claim is deleted or rewritten, and
  `resolved` is never flipped on the stored analysis.** The payload's sha256 is
  unchanged after a finding; the finding is appended beside it. The analysis
  layer already forbids a draft from arriving pre-resolved, so only a person
  can ever do this.
- **Nothing infers whether two statements agree.** No string or numeric
  comparison decides it. Comparing `15min` with `00:15:00` and merging them
  would be repairing the document on a guess.
- **The default is blocked.** With nothing recorded the answer is False, and a
  withdrawal restores the block.
- **A finding never touches the safety gate**, and acknowledging the safety
  gate never settles an ambiguity. Two decisions, two records.

Revocation is real: the earlier finding is not erased, because the ledger is
append-only, so who decided what and who withdrew it both stay readable. A
reviewer may withdraw and record a different finding; the identifier carries an
ordinal so the second decision does not collide with the first.

Measured on the real document, all four directions:

```
before any finding           : blocked
all four resolved            : cleared
one withdrawn                : blocked again
that one re-decided distinct : blocked
```

## 8a. The wall after resolution: repeat-until, alone

The walk was rerun through the audited route with nothing stepped around.
Ambiguities all settled, safety warnings confirmed, and **stage 8 still
refuses** - now on `unsupported_repeat_until` alone, which no acknowledgement
or finding clears. Reported as it stands rather than worked around.

| Stage | Before STEP 16 | After |
| --- | --- | --- |
| 6 readiness | ambiguity x4 + safety + repeat-until x2 | same |
| 7 safety confirmation | ok | ok |
| 7b ambiguity findings | **did not exist** | **4 of 4 settled** |
| 8 development activation | blocked by all three classes | **blocked by repeat-until only** |

## 8b. The two repeat-until statements, quoted

Facts and options; no policy change is made here.

**First**, page 5, attached to step 7:

> 7 Repeat steps 2-7 until the gel band is fully destained.

Its Expected-result block adds: *"It is really important that the gel bands are
fully destained before progressing to the next step. This is usually attained
by the end of two cycles of solution A/B washes."*

**Second**, page 6, attached to step 9. The claim's cited excerpt is:

> 9 Remove and discard the acetonitrile. Your gel band should have a whitish
> appearance when dry.

The repeat instruction itself is **not in that excerpt** - it is in the
following Expected-result prose: *"If the band is still transparent then repeat
steps 8-9 until fully dehydrated."* So the second construct is attached to a
step whose quoted evidence does not contain the repeat sentence. Recorded as a
fact.

**What the statements ask an operator to do.** Neither states a repetition
count. Both stop on a **visual judgement made by a person**: "fully destained"
(the first, with a hint that two cycles usually suffice) and "still
transparent / fully dehydrated" (the second). Both name a **range** of steps to
repeat - 2-7 and 8-9 - while the assembled constructs record
`repeated_step_ids` as only the enclosing step, `step-7` and `step-9`
respectively. So the range in the source is not represented.

**Why P1 declines it.** The policy's supported feature set excludes
repeat-until because the loop has no bound the server can compute and no
condition the server can evaluate. The stop condition is an unaided human
observation, so the server cannot know whether another iteration is required,
cannot know when to stop, and cannot bound the total work. Advancing a step on
an unevaluable condition would also put the model or the code in the position
of deciding an execution question, which is the boundary this system exists to
hold.

**If a reviewer converted it to a bounded repetition plus an observation
checkpoint** - what would and would not be guaranteed:

- Guaranteed: termination, because an explicit upper bound exists; a recorded
  human confirmation at each iteration boundary; and an audit trail of who
  confirmed what.
- Not guaranteed: that the bound matches the protocol's intent. "Usually
  attained by the end of two cycles" is a hint, not a limit, and a bound of two
  would silently stop short on a gel needing three. The operator would then be
  told the loop is finished when the source's own condition is unmet.
- Also not guaranteed: that the observation is correct. The system records that
  a person said "destained"; it cannot verify it. The confirmation moves the
  judgement onto a named person, which is the intended place for it, but it
  adds no evidence.
- Not addressed at all: the step range. A bounded repetition over `step-7`
  alone is not what "repeat steps 2-7" asks for, and re-running one step in
  place of six would be wrong in a way the operator might not notice.

**To make an unbounded loop impossible** the following would be needed: an
explicit maximum iteration count carried on the construct and enforced by the
server before execution starts; a refusal at assembly when a repeat-until has
no bound rather than a refusal only at readiness; a recorded human confirmation
per iteration, with the loop blocked rather than advanced when it is absent;
and the repeated step range represented correctly, since a bound on the wrong
steps bounds the wrong thing.

## 8c. Regression: every stored payload is loaded

Adding one field made an analysis written months earlier undecodable, and 1145
tests missed it - because every one of them wrote its payload with the same
code that read it back. Only data that predates the change can catch that.

`tests/test_stored_payloads_still_load.py` now reads what is actually on disk:
it discovers every `*.sqlite` under the runtime directory, opens each read-only
and immutable, and decodes every row of `analysis_payloads`. The list is
discovered rather than maintained, so a store added later is picked up
automatically - it already found a second store, in a backup directory, that no
hand-written list would have included. Decoding is not enough on its own, so it
also validates each decoded Protocol and checks the steps carry their evidence.

**Confirmed against the defect**: restoring STEP 14's exact-field-match decoder
makes it fail, on both stores, naming the payload digest; with the fix it
passes. It also pins the other half of the rule - a field with no default that
is missing, or a field the dataclass does not have, is still refused, so
tolerating an added field does not tolerate a truncated record.

## 8d. The demo does not use the gated route

Recorded in code at both `CuratedProtocolSession` and the replay harness, and
here. `ProtocolCatalog.load_executable_fixture` builds a fixture from a stored
analysis and refuses unless the Protocol is approved or development-activated.
Direct construction, which the replay harness and the demo use, has no such
check. Nothing produced by the analysis pipeline has ever come through the
gated route, so direct construction is currently the only route carrying
anything at all.

**Not moved yet, on purpose.** There is nothing to move it to until one
executable Protocol exists, and for this document that waits on the
repeat-until decision above.

The two local protocol sources supplied for measurement are in `.gitignore`
(24 MB and 5.1 MB). Their sha256 are pinned in tests, which skip with the
expected hash printed when a file is absent, so a missing source is loud.

## 9. Repeat policy, recorded and not yet built

The decision, taken by the project owner and recorded here so implementation
cannot drift from it:

- **A server-set bound is never a completion condition.** Inventing a
  completion condition the source does not state is forbidden, and "usually
  attained by the end of two cycles" is a hint, not a specification. A bound of
  two used as completion would tell an operator "finished" on a gel that needs
  three - a false completion notice, the worst failure this product can
  produce.
- Authority to end a repetition rests on **a person's observation**.
- A bound exists **only as a runaway guard**.
- Reaching the bound is **a halt and an escalation, never a completion**.
- Each iteration records a human confirmation, and progress blocks without one.

Execution of this is deliberately not implemented in this step: the
preconditions below had to come first, because a bound placed on a wrongly
recorded range would bound the wrong thing.

## 9a. headspace: the loop still does not close, and why

Same walk as STEP 15, on `usingdynamicheadspacecollections.pdf`, offline, zero
provider calls, isolated store. **A plumbing check, not a quality
measurement** - the claim model is the deterministic fixture, which is in scope
here (62 labels, strictly increasing, no duplicate or descent).

| Stage | Result |
| --- | --- |
| 1 extraction | ok - 16 pages, verified |
| 2 page admission | ok - 5 chunks |
| 3 chunk analysis | ok - 5/5 |
| 4 whole-document merge | ok - 93 claims |
| 5 assembly | ok - 62 steps |
| 6 readiness | ok - `analysis_required` |
| 7 safety confirmation | ok |
| 7b ambiguity findings | **0 ambiguities on this document** |
| 8 development activation | **STOPS** - `unsupported_repeat_until` x2 |

**The premise that headspace has no conditional repetition is correct, and the
blockers are misclassifications.** All five repeat mentions in the source are
unconditional:

```
p5  step 16 : Repeat steps 12-15 twice more, to wash bacterial cells.
p5  step 21 : Repeat steps 19-20 for the required number of ... replicates
p6  step 27 : Repeat steps 23-26 for the metal plates.
p12 step 42 : repeat steps 36-41 twice more (three conditioning rounds in total)
p14 step 51 : Repeat steps 43-50 for the required number of treatments
```

Not one has a conditional stop. Two of them nevertheless became `RepeatUntil`
constructs - a fixed count ("twice more") and a change of target ("for the metal
plates").

The cause is the claim vocabulary. `ClaimCategory` has exactly one repetition
category, `repeat_condition`, and assembly maps it to `RepeatUntil`. P1's
supported feature set is `{fixed_range_repetition, informational_difference}`,
so **the policy would accept a bounded repetition and the contract has no way
to say one.** Every repetition, bounded or not, is forced into the one shape the
policy refuses.

Nothing was relaxed to get past this, and the gap is not closed here.
Distinguishing "twice more" from "until destained" is a semantic reading of
prose; doing it in code would mean a word list, which this design has rejected
repeatedly. It belongs in the claim vocabulary as a category the provider
chooses, and that is a contract change to decide, not to slip in.

So **the loop is still not closed by any document.** The nearest miss is
headspace: one contract gap away, with no conditional repetition and no
ambiguity in the way.

## 9b. in-gel's third repeat: declined, not lost

The source states three conditional repeats; the pipeline produced two.
Traced, facts only:

- The p8 sentence *"If the band is still transparent then repeat steps 17-18
  until fully dehydrated"* lives in one segment, and that segment was
  **explicitly declined** by the model. Not unaccounted: page 8 has 0
  unaccounted segments and status `complete`.
- **So this is not a silent omission.** The all-segment accounting did its job -
  the segment is on the record as declined and visible to a reviewer, which is
  exactly what that obligation was built for.
- No construct was created because the fixture detects a repeat only inside a
  per-page step-block excerpt, and on page 8 the sentence sits **before the
  page's first label** (21). Its owning step, 19 or 20, is on page 7, so the
  sentence falls outside every step block the fixture builds for page 8. On
  page 6 the equivalent sentence sits inside step 9's block, which is why that
  one fired.

**Classification: a limitation of the offline fixture model, not a hole in a
server rule.** The rule that would have caught a silent loss caught it.
Recorded, not fixed, as instructed.

## 9c. Two preconditions for any repeat bound, fixed

**The range was not represented.** All three in-gel statements name ranges
(2-7, 8-9, 17-18) and `repeated_step_ids` held only the enclosing step, so
"repeat steps 2-7" was recorded as repeating step 7 alone. Bound that and an
operator re-runs one step where the protocol asks for six - a different
experiment, recorded without anyone being told.

A `repeat_condition` claim must now declare `repeated_step_labels`, the first
and last step label the source states. The server expands it to every step in
the range and refuses rather than trimming:

| Fault | Outcome |
| --- | --- |
| no declared range | refused, `repeat_range_missing` |
| range runs backwards | refused, `repeat_range_inverted` |
| a label no step carries | refused, `repeat_range_step_unknown` |
| partly resolvable range | refused, never trimmed to the part that resolves |

Measured on in-gel, `repeat-p5-3` now covers `step-2 … step-7`, six steps
instead of one.

**The evidence did not contain the instruction.** The p6 construct cited *"9
Remove and discard the acetonitrile…"* while the repeat sentence sat in the
following prose. A construct asserting a repetition whose evidence contains no
repetition is an evidence-integrity break.

The enforcement is a shape, not a vocabulary: **the cited excerpt must contain
the two declared labels written as a range** - two numbers joined by a hyphen or
dash. Nothing reads the words "repeat" or "steps"; the claim says which range
it means and the server checks the excerpt for exactly that. Hyphen, non-
breaking hyphen, en dash, em dash and minus all count, spacing is tolerated,
and `170-180` does not satisfy a declared `17-18`.

Stated in the prompt and the schema description exactly as enforced, with the
exemptions named, and bound into the parity audit by five new reason codes so
the regex and the prompt cannot drift. `CLAIM_SCHEMA_VERSION` moves 6 to 7: a
response written against 6 carries no `repeated_step_labels` and is refused, so
declaring 6 would misstate the shape.

The fixture was corrected too, since it must cite what it claims: it now cites
the segment carrying the repeat sentence and declares the range read from that
sentence. On in-gel, `repeat-p6-0` now quotes *"…repeat steps 8-9 until fully
dehydrated"* rather than step 9's instruction.

## 9d. What headspace would cost with a real provider

5 chunks, one call each, so the floor is **5 calls** if every chunk validates
first time. Merge requires all five, so one unrecovered failure ends the run.

The only measured per-chunk first-attempt rate remains ANKOM's **4 of 8** under
the current contract - a larger, denser document, so indicative rather than a
rate for headspace, and still the only measurement that exists. Taking
p = 0.5:

| Retry budget per chunk | Calls used | P(all 5 validate) | Expected calls |
| --- | --- | --- | --- |
| 0 (harness default) | 5 | 3.1% | 5 |
| 1 | 5-10 | 24% | ~7.5 |
| 2 | 5-15 | 51% | ~8.8 |
| 3 | 5-20 | 72% | ~9.5 |

Two calls of authorized budget remain, well below the floor of 5. A further
consideration that is not a call cost: the claim schema now requires a declared
repeat range, so a provider run would also be the first test of whether a model
can state one.


## 5. Narrowing `no_relevant_claims`

`:1857` accepts `NO_RELEVANT_CLAIMS` whenever `expected_ids` is empty. A
provider that emits nothing for a page therefore passes that page
unconditionally, with no server cross-check. 8 of ANKOM's 40 pages take this
path.

This is not a violation of the server-authority principle — semantic judgement
is provider-owned by design. It is a **verification gap**: the flag's blast
radius is an entire page, and nothing bounds it.

Proposed: **narrow, do not remove.**

- Remove `NO_RELEVANT_CLAIMS` as a page-wide exemption; replace it with a
  per-segment disposition, so the unit of "nothing here" matches the unit of
  accounting.
- **Keep `ANALYSIS_INCOMPLETE` at page level.** It is a fail-closed signal that
  already blocks merge, and page granularity is right for "I could not finish
  this page".
- Add the unit cross-check above, so a segment carrying a value can never be
  dispositioned away.

Removing the exemption outright would be wrong. Genuinely empty regions exist —
running footers, the `.` orphan, `text_empty` pages — and forcing a claim there
pressures the provider to invent one, which contradicts fail-closed over
guessing. **Accounting is not claiming**: the obligation is to say something
about every segment, not to assert something about every segment. Preserving
that distinction is what keeps the obligation honest.

## 5a. Segmentation-degradation detector — reinstated

An earlier review deprecated this indicator on two grounds. Both are wrong, and
both were checked against the current extractor.

**"The extraction cross-check already covers it" — no.** The cross-check asks
whether two engines *read* the page differently. A page with no sentence
terminator is read identically by both, so it is reported `verified`. Both local
sources verify as a whole, and the degraded pages below are inside them. The
cross-check carries no signal about whether a page can be *segmented*.

**"All-segment accounting already covers it" — no.** If a page collapses to one
segment, the accounting obligation for that page is one segment, and a single
claim discharges it. Everything else in the segment — including a hazard block —
is absorbed exactly as it is today. The earlier reasoning conflated "every
segment is accounted for" with "every content unit is accounted for"; when
segmentation fails, those two diverge completely.

Measured: pages that collapse to a single segment under R2 are ANKOM 7, 11, 14,
17, 18, 40 and in-gel 1. in-gel p1 is 1232 characters across 24 lines reduced to
one segment; ANKOM p17 is 305 characters across 12 lines. On each, one claim
would satisfy the whole page.

This matters beyond these two documents: R2 keys on end-of-line
`[.!?]`, and end-of-line sentence punctuation is not universal. Korean protocol
text, bullet lists and table-shaped pages routinely lack it, and on such a
document R2 silently reverts to today's behaviour with no signal.

### Retained definition (fail-closed gate)

```
segmentation_degraded(page) :=
    line_count(page) >= 5  AND  segment_count(page) <= 1
```

Deterministic, vocabulary-free, and computable server-side before the provider
request is built, so a page it flags can be refused or marked
`analysis_incomplete` rather than silently under-segmented.

Discriminative power on the current extractor:

| Source | Flagged | Pages | What they are |
| --- | --- | --- | --- |
| ANKOM | **1 / 40** | p17 | equipment metadata list (`Oven NAME` / `BRAND` / `SKU`), no sentence punctuation |
| in-gel | **1 / 9** | p1 | title / DOI / author cover page |

Both are genuinely unsplittable, so the gate is neither always-on nor
always-off.

### Rejected variant, and why

A stricter form — flag when any single segment absorbs 8 or more lines — fires
on **15 / 40** ANKOM pages and 3 / 9 in-gel pages. Those are mostly legitimate
equipment/metadata line lists. At 37% of a document it is not a gate; it is
noise. It is retained only as an **advisory metric**, `absorbed_lines` = the
maximum line count inside one segment (ANKOM max 21, in-gel max 23), reported by
the audit and never used to block.

## 5b. Segmentation-degradation detector as implemented

`degraded_segmentation_pages(extraction, source_revision=...)`:
`line_count >= MIN_LINES_FOR_SEGMENTATION (5)` and `segment_count <= 1`.
Measured, matching the STEP 2 prediction exactly: **ANKOM (17,)** — an
equipment metadata list — and **in-gel (1,)** — the title/DOI/author cover.

**Surfaced as a diagnostic record, not a gate.** The audit runner reports
`degraded_segmentation_pages` per source. Refusing request construction or
adding a readiness reason were both rejected: every real protocol has a cover
page, so either would fire on every document and become the always-on gate that
makes acknowledgement a formality — the same failure argued against for a
prerequisite gate. It becomes decision-relevant in STEP 4, where a degraded
page's single segment must still be dispositioned and cannot be `no_claim` if
it carries a unit-bearing value.

The `absorbed_lines >= 8` variant remains rejected as a gate: 15 of 40 ANKOM
pages, mostly legitimate metadata lists.

## 6. Cardinality, versioning, regressions, test helper

### Cardinality — ample headroom

| Bound | Limit | Today | Under R2 + accounting |
| --- | --- | --- | --- |
| `evidence_item_ids` / page (`:46`) | 256 | 12 (ANKOM), 23 (in-gel) | floor 17 (ANKOM), 11 (in-gel) |
| `page_coverage` records / chunk (`:45`) | 32 | 8 (ANKOM), 5 (in-gel) | unchanged — per page, not per segment |
| segments / page | — | 5, 9 | 18, 11 |

The obligation floor is the maximum substantive-segment count per page, since
each needs at least one claim or disposition. At 17 against a limit of 256 there
is roughly a 14× margin even before parameter claims are added.
`MAX_PAGE_COVERAGE_RECORDS` is driven by core pages per chunk
(`_HARD_MAX_CORE_PAGES_PER_CHUNK = 32`) and is unaffected by segmentation.

### Version bumps

- **`CLAIM_SCHEMA_VERSION` 5 → 6: required.** The `no_claim` disposition is a
  new field in the provider response, which is a shape change. If the design
  were narrowed to accounting-only with no new response field, this bump would
  not be needed.
- **`EVIDENCE_SEGMENT_VERSION` 3 → 4: required.** Boundary derivation changes,
  and this constant exists to version exactly that; it is already inside the
  segment identity hash.

### Tests that will break

| Location | Why |
| --- | --- |
| `tests/test_protocol_claim_analysis.py:752-798` `test_zero_item_core_page_requires_no_relevant_claims_status` | encodes page-level status semantics directly |
| `tests/test_protocol_chunk_analysis.py:205` | fixture emits `"complete" if item_ids else "no_relevant_claims"` |
| `scripts/prototype_claim_chunks.py` `ExactNumberedStepClaimModel` | emits only numbered-step and parameter claims; cannot satisfy segment accounting without a disposition emitter |
| `scripts/audit_claim_semantics.py` | consumes that model |
| every `CLAIM_SCHEMA_VERSION` assertion | the bump |
| `scripts/diagnose_protocol_claim_latency.py` | needs re-running |

Surviving unchanged: the `numbered_action_missing` tests (`:1421`, `:1455`),
because the design keeps that invariant separate. That is the main reason to
keep it separate.

### Multi-line test helper — prerequisite, not optional

`write_pages` (`tests/test_protocol_claim_analysis.py:80`) replaces `"\n"` with
`" "`, so every synthetic fixture page has **zero newlines**. R2 adds boundaries
only at line breaks, so it contributes nothing on any current fixture: the new
rule would ship completely unexercised. This is the largest quality risk in the
whole change.

Proposed helper, alongside the existing one rather than replacing it:

```python
_FIXTURE_LINE_LEADING = 12  # points between baselines

def write_lined_pages(path, pages):
    """Write each page as real separate text lines.

    Emits one Td-positioned show operation per line instead of collapsing
    newlines to spaces, so extracted text contains actual line breaks and
    boundary rules that key on them are exercised.  Page width stays
    _FIXTURE_PAGE_WIDTH so no line is clipped.
    """
```

Each page is a `tuple[str, ...]` of lines; the writer emits
`BT /F1 9 Tf 36 <y> Td (line) Tj ET` per line, decrementing `y`. Existing
`write_pages` callers stay untouched; new boundary tests use the new helper and
assert on segment counts and on which segment a given value lands in.

## 7. `data/runtime` handling — proposal only, not executed

The pilot store's single analysis revision is `curated-c2779c…`, materialized by
`bootstrap_development_fixture` from the curated fixture whose `fixture_sha256`
is now `fb869290…`. Nothing has been executed against `data/runtime`.

On re-bootstrap:

- **Created:** a new analysis revision keyed `curated-fb869290…`, plus its
  `analysis_payloads` row and a `development_fixture_materialized` event.
- **Retained:** the old `curated-c2779c…` revision and every existing event.
  `append_analysis_revision` is keyed by analysis id and the ledger is
  append-only, so nothing is overwritten or deleted.
- **Consequence:** two analysis revisions for one protocol revision, the older
  one referencing the superseded fixture. `_latest_analysis` selects the newer,
  so behaviour follows the corrected fixture.

Risk is low specifically because **zero analyses are approved for execution**.
No approval event, no acknowledgement, and no execution authorization is
invalidated, because none exists to invalidate. The old revision becomes inert
history rather than a live conflict. Doing this after any approval exists would
be materially riskier, since approval is bound to an analysis revision number —
which is the argument for re-bootstrapping before pilot approvals begin, not
after.

## 8. Integration debt

`main` does not exist locally and `origin` publishes only
`refactor/voice-workflow-agent-stability`, so the integration target is that
branch. Divergence from it: **4 behind, 23 ahead** (merge base `ba9751d`).

### rescue/* status — all already integrated

All **22** `rescue/*` branches are ancestors of HEAD. There is no unmerged
rescue work; the branches are historical markers of already-integrated commits
and can be deleted once HEAD lands. This contradicts the premise that 17 remain
unmerged.

### The 4 commits HEAD lacks

```
f5e9f36 Revert "Complete Korean bench and pilot readiness hardening"
97ca557 Complete Korean bench and pilot readiness hardening
1525034 Add the lab identity and Google sign-in foundation
ed55f71 Harden noisy-lab voice input and finish the researcher/reviewer/admin experience
```

`f5e9f36` reverts `97ca557`, so those two cancel. The real content HEAD is
missing is `1525034` (identity / Google sign-in) and `ed55f71` (noisy-lab voice
input, researcher/reviewer/admin experience).

### Conflict scope — measured by dry-run merge

`git merge-tree` (no working-tree change): **10 conflicted files** out of 51
changed on their side and 59 on ours; 7 overlapping files auto-merge cleanly.

| Conflicted | Their churn | Our churn |
| --- | --- | --- |
| `src/voice_workflow_agent/server.py` | 997+/49- | 397+/26- |
| `src/voice_workflow_agent/static/index.html` | 715+/205- | 153+/132- |
| `src/voice_workflow_agent/curated_protocol.py` | 834+/174- | 379+/49- |
| `src/voice_workflow_agent/protocol_catalog.py` | 555+/32- | 355+/85- |
| `README.md` | 204+/9- | 160+/3- |
| `src/voice_workflow_agent/static/app.css` | 178+/11- | 122+/0- |
| `tests/e2e/{admin,researcher,reviewer}.spec.ts` | 59–182+ | 3–117+ |
| `tests/test_frontend.py` | 160+/29- | 39+/17- |

Auto-merging cleanly: `.env.example`, `AGENTS.md`, `experiment_protocol.py`,
`tests/conftest.py`, `test_curated_protocol_cascade.py`,
`test_experiment_protocol.py`, `test_protocol_catalog.py`.

The conflict is concentrated in UI and server wiring, not in the claim/evidence
pipeline: `protocol_claim_analysis.py`, `protocol_chunk_analysis.py` and
`experiment_protocol_pdf.py` are touched by our side only and do not conflict.

### Suggested strategy — not executed

1. Land the 4 commits into HEAD first (`merge refactor/... into HEAD`), not the
   reverse, so the extraction and evidence work stays on the mainline of
   development and conflicts are resolved once.
2. Resolve in dependency order: `curated_protocol.py` and `protocol_catalog.py`
   before `server.py`, then `index.html` / `app.css`, then the e2e specs last —
   the specs assert on UI that the earlier resolutions determine.
3. Treat `README.md` as a merge of two additive sections, not a conflict to pick
   a side of.
4. Re-run the full suite plus the Playwright CI config after resolution;
   `test_frontend.py` and the three e2e specs are the ones most likely to need
   real edits rather than mechanical resolution.
5. Delete the 22 `rescue/*` branches after the merge lands.
6. Do not rebase: 23 commits with a corrected-extraction data migration inside
   them would replay the fixture migration against changing text.
