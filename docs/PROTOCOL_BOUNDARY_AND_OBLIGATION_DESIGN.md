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
