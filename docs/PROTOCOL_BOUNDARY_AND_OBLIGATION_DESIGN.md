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
