"""Deterministic semantic-quality audit over validated protocol claims.

Canonical claim validation (``protocol_claim_analysis``) proves that every
admitted claim is *structurally* sound and *exactly* backed by server-owned
source evidence.  It deliberately proves nothing about scientific meaning: a
chunk can satisfy every canonical invariant while silently dropping a drying
temperature, a hazard block, or a repeat count.

This module measures that remaining gap.  It is a **measurement, not a gate**:
it never mutates claims, never relaxes canonical validation, and is not wired
into the admission path.  Callers decide what to do with the findings.

Every expectation is derived independently from the immutable extracted source
text, never from the claim set under audit and never from provider output, so
the same audit applies unchanged to a deterministic offline fixture and to a
live provider response.  Detection is unit- and cue-driven only; it encodes no
document-specific pages, labels, section names, or counts.

Soundness contract: every check reports in one direction only, the direction
that stays valid under imperfect detector recall.  Real protocol text renders
values in forms this module deliberately does not try to parse (colon-stripped
``HHMMSS`` timers, ``O/N``, degree-less ``60C``), so the audit may miss a
defect but must never invent one.  Two consequences follow, and both are
deliberate:

* A surplus of claims over detected values is *not* reported, because it is
  indistinguishable from this module's own limited recall.
* Duplicate claims are *not* detectable at all here.  Canonical evidence
  segments are whole numbered-action blocks, so two parameter claims on one
  step share byte-identical canonical source text; nothing in canonical state
  distinguishes a duplicate from two genuinely different values.  That is a
  property of evidence granularity, not an oversight -- see
  ``VALUE_SPAN_NOT_ISOLATED``, which measures it directly.

Step attribution is likewise absent by design: ``validate_whole_protocol_claims``
already fails closed when a parameter claim's step does not match its target
action, so re-checking it here would add nothing.

The unit vocabulary here is intentionally separate from
``protocol_claim_analysis._VALUE_UNITS``, which exists for a different purpose
(guarding numbered-action line detection) and is not a value-extraction
vocabulary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .experiment_protocol_pdf import ProtocolPdfExtraction
from .protocol_claim_analysis import (
    ClaimCategory,
    MergedProtocolClaims,
    ProtocolChunkClaimAnalysis,
    ProtocolClaim,
    generate_page_evidence_segments,
)

MAX_AUDIT_FINDINGS = 512
MAX_FINDING_EXCERPT_CHARS = 120


class SemanticFindingSeverity(str, Enum):
    """How a finding bears on safe execution of the protocol."""

    CRITICAL = "critical"
    ADVISORY = "advisory"


class SemanticFindingCode(str, Enum):
    VALUE_NOT_REPRESENTED = "value_not_represented"
    VALUE_SPAN_NOT_ISOLATED = "value_span_not_isolated"
    HAZARD_NOT_REPRESENTED = "hazard_not_represented"
    PREREQUISITE_NOT_REPRESENTED = "prerequisite_not_represented"
    REPEAT_CONDITION_NOT_REPRESENTED = "repeat_condition_not_represented"
    CLAIM_LOST_IN_ASSEMBLY = "claim_lost_in_assembly"


_SEVERITY_BY_CODE: Mapping[SemanticFindingCode, SemanticFindingSeverity] = {
    SemanticFindingCode.VALUE_NOT_REPRESENTED: SemanticFindingSeverity.CRITICAL,
    SemanticFindingCode.VALUE_SPAN_NOT_ISOLATED: SemanticFindingSeverity.CRITICAL,
    SemanticFindingCode.HAZARD_NOT_REPRESENTED: SemanticFindingSeverity.CRITICAL,
    SemanticFindingCode.PREREQUISITE_NOT_REPRESENTED: (
        SemanticFindingSeverity.ADVISORY
    ),
    SemanticFindingCode.REPEAT_CONDITION_NOT_REPRESENTED: (
        SemanticFindingSeverity.CRITICAL
    ),
    SemanticFindingCode.CLAIM_LOST_IN_ASSEMBLY: SemanticFindingSeverity.CRITICAL,
}

# Ordered most-specific-first; matched case-sensitively so that ``mM``
# (millimolar) never absorbs ``mm`` (millimetre).  Bare ``M`` is deliberately
# excluded: in real protocol text it is ambiguous between molar and a minute
# shorthand, and an audit that guesses is worse than one that abstains.
_VALUE_UNIT_PATTERNS: tuple[tuple[ClaimCategory, str], ...] = (
    (
        ClaimCategory.CONCENTRATION,
        r"mg\s*/\s*m[lL]|ng\s*/\s*[µμu][lL]"
        r"|[µμu]g\s*/\s*m[lL]|g\s*/\s*[lL]",
    ),
    (ClaimCategory.CONCENTRATION, r"mM|nM|[µμu]M|%"),
    (ClaimCategory.AGITATION_SPEED, r"rpm|[x×]\s*g"),
    (ClaimCategory.TEMPERATURE, r"°\s*[CF]|degrees?\s+[CF]"),
    (
        ClaimCategory.QUANTITY,
        r"mm3|mm|cm|m[lL]|[µμu][lL]|mg|kg",
    ),
    (
        ClaimCategory.DURATION,
        r"hours?|hrs?|minutes?|mins?|min|seconds?|secs?|days?|weeks?|h|s",
    ),
    (ClaimCategory.QUANTITY, r"[Ll]|g"),
)

_VALUE_CATEGORIES: frozenset[ClaimCategory] = frozenset(
    category for category, _ in _VALUE_UNIT_PATTERNS
)

_VALUE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:[0-9]+(?:[.,][0-9]+)?)\s*(?:"
    + "|".join(
        f"(?P<unit{index}>{pattern})"
        for index, (_, pattern) in enumerate(_VALUE_UNIT_PATTERNS)
    )
    + r")(?![A-Za-z0-9])"
)

_CATEGORY_BY_GROUP: Mapping[str, ClaimCategory] = {
    f"unit{index}": category
    for index, (category, _) in enumerate(_VALUE_UNIT_PATTERNS)
}

# Generic protocol-language cues.  These are intentionally conservative: a cue
# that fires on ordinary prose would bury real omissions in noise.
_CUE_PATTERNS: tuple[tuple[ClaimCategory, SemanticFindingCode, str], ...] = (
    (
        ClaimCategory.WARNING_HAZARD,
        SemanticFindingCode.HAZARD_NOT_REPRESENTED,
        r"\b(?:WARNING|CAUTION|DANGER|hazard(?:ous)?|toxic|corrosive"
        r"|flammable|irritant|explosive|carcinogen(?:ic)?)\b",
    ),
    (
        ClaimCategory.PREREQUISITE,
        SemanticFindingCode.PREREQUISITE_NOT_REPRESENTED,
        r"\b(?:before\s+start|before\s+you\s+begin|prerequisite)\b",
    ),
    (
        ClaimCategory.REPEAT_CONDITION,
        SemanticFindingCode.REPEAT_CONDITION_NOT_REPRESENTED,
        r"\b(?:repeat|twice|thrice|[0-9]+\s+times)\b",
    ),
)

_COMPILED_CUES: tuple[
    tuple[ClaimCategory, SemanticFindingCode, re.Pattern[str]], ...
] = tuple(
    (category, code, re.compile(pattern, re.IGNORECASE))
    for category, code, pattern in _CUE_PATTERNS
)


@dataclass(frozen=True)
class SourceValueToken:
    """One execution-critical value literal found in immutable source text."""

    source_page_number: int
    start: int
    end: int
    category: ClaimCategory
    text: str


@dataclass(frozen=True)
class SemanticAuditFinding:
    code: SemanticFindingCode
    severity: SemanticFindingSeverity
    source_page_number: int
    expected_category: ClaimCategory | None = None
    observed_category: ClaimCategory | None = None
    claim_id: str | None = None
    step_id: str | None = None
    expected_count: int | None = None
    observed_count: int | None = None
    source_excerpt: str = ""

    def public_dict(
        self,
        *,
        include_source_excerpts: bool = False,
    ) -> dict[str, object]:
        """Render the finding, omitting source text unless explicitly asked.

        Findings quote laboratory source documents, which may be private, so
        the content-free shape is the default (see ``AGENTS.md`` rule 6).
        """

        payload: dict[str, object] = {
            "code": self.code.value,
            "severity": self.severity.value,
            "source_page_number": self.source_page_number,
            "expected_category": (
                self.expected_category.value
                if self.expected_category is not None
                else None
            ),
            "observed_category": (
                self.observed_category.value
                if self.observed_category is not None
                else None
            ),
            "claim_id": self.claim_id,
            "step_id": self.step_id,
            "expected_count": self.expected_count,
            "observed_count": self.observed_count,
        }
        if include_source_excerpts:
            payload["source_excerpt"] = self.source_excerpt
        return payload


@dataclass(frozen=True)
class SemanticAuditReport:
    """Content-free-by-default measurement of claim semantic quality."""

    source_sha256: str
    audited_page_numbers: tuple[int, ...]
    audited_claim_count: int
    value_tokens_detected: int
    value_tokens_represented: int
    findings: tuple[SemanticAuditFinding, ...]
    findings_truncated: bool = False

    @property
    def critical_findings(self) -> tuple[SemanticAuditFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.severity is SemanticFindingSeverity.CRITICAL
        )

    @property
    def is_semantically_clean(self) -> bool:
        """True when no critical finding survived and nothing was truncated."""

        return not self.critical_findings and not self.findings_truncated

    def counts_by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.code.value] = counts.get(finding.code.value, 0) + 1
        return counts

    def public_dict(
        self,
        *,
        include_source_excerpts: bool = False,
    ) -> dict[str, object]:
        return {
            "source_sha256": self.source_sha256,
            "audited_page_numbers": list(self.audited_page_numbers),
            "audited_claim_count": self.audited_claim_count,
            "value_tokens_detected": self.value_tokens_detected,
            "value_tokens_represented": self.value_tokens_represented,
            "critical_finding_count": len(self.critical_findings),
            "findings_truncated": self.findings_truncated,
            "counts_by_code": self.counts_by_code(),
            "is_semantically_clean": self.is_semantically_clean,
            "findings": [
                finding.public_dict(
                    include_source_excerpts=include_source_excerpts
                )
                for finding in self.findings
            ],
        }


def detect_source_value_tokens(
    page_text: str,
    *,
    source_page_number: int,
) -> tuple[SourceValueToken, ...]:
    """Find every unit-bearing value literal on one immutable source page."""

    tokens: list[SourceValueToken] = []
    for match in _VALUE_TOKEN.finditer(page_text):
        category = next(
            _CATEGORY_BY_GROUP[name]
            for name in _CATEGORY_BY_GROUP
            if match.group(name) is not None
        )
        tokens.append(
            SourceValueToken(
                source_page_number=source_page_number,
                start=match.start(),
                end=match.end(),
                category=category,
                text=match.group(0),
            )
        )
    return tuple(tokens)


def _excerpt(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_FINDING_EXCERPT_CHARS:
        return collapsed
    return collapsed[: MAX_FINDING_EXCERPT_CHARS - 1] + "…"


def _claims_by_page(
    claims: Iterable[ProtocolClaim],
) -> dict[int, tuple[ProtocolClaim, ...]]:
    grouped: dict[int, list[ProtocolClaim]] = {}
    for claim in claims:
        grouped.setdefault(claim.evidence.source_page_number, []).append(claim)
    return {key: tuple(value) for key, value in grouped.items()}


def _page_segment_offsets(
    extraction: ProtocolPdfExtraction,
    *,
    source_revision: str,
    page_number: int,
) -> dict[str, tuple[int, int]]:
    """Rebuild each canonical evidence segment's exact page offsets."""

    offsets: dict[str, tuple[int, int]] = {}
    cursor = 0
    for segment in generate_page_evidence_segments(
        extraction,
        source_revision=source_revision,
        page_number=page_number,
    ):
        end = cursor + len(segment.text)
        offsets[segment.segment_id] = (cursor, end)
        cursor = end
    return offsets


def _claim_span(
    claim: ProtocolClaim,
    offsets: Mapping[str, tuple[int, int]],
) -> tuple[int, int] | None:
    spans = [
        offsets[segment_id]
        for segment_id in claim.evidence.evidence_segment_ids
        if segment_id in offsets
    ]
    if not spans:
        return None
    return (min(start for start, _ in spans), max(end for _, end in spans))


def _value_key(token: SourceValueToken) -> str:
    """Normalize a value literal so spacing and case do not split one value.

    ``20 g`` and ``20g`` are the same quantity; counting them separately would
    manufacture a deficit.  Case folding is safe only because comparison never
    crosses categories -- ``mM`` and ``mm`` are concentration and quantity
    respectively and are never keyed against each other.
    """

    collapsed = "".join(token.text.split()).casefold()
    return collapsed.replace(",", ".").replace("μ", "µ").replace("u", "µ")


def _tokens_within(
    tokens: Iterable[SourceValueToken],
    span: tuple[int, int],
) -> tuple[SourceValueToken, ...]:
    start, end = span
    return tuple(
        token for token in tokens if token.start >= start and token.end <= end
    )


def _audit_value_tokens(
    extraction: ProtocolPdfExtraction,
    page_numbers: Iterable[int],
    by_page: Mapping[int, tuple[ProtocolClaim, ...]],
) -> tuple[list[SemanticAuditFinding], int, int]:
    """Compare source value literals against the claims that should carry them.

    Canonical evidence segments are whole numbered-action blocks, so a claim's
    reconstructed ``source_text`` is block-granular.  Auditing by substring
    containment alone would therefore let one claim vacuously "represent" every
    value in its step.  These checks instead work on exact segment offsets and
    on per-step counts, which stay sound at that granularity.
    """

    findings: list[SemanticAuditFinding] = []
    detected = 0
    represented = 0
    for page_number in page_numbers:
        page_text = extraction.pages[page_number - 1].text
        page_claims = by_page.get(page_number, ())
        tokens = detect_source_value_tokens(
            page_text,
            source_page_number=page_number,
        )
        detected += len(tokens)
        if not page_claims:
            findings.extend(
                SemanticAuditFinding(
                    code=SemanticFindingCode.VALUE_NOT_REPRESENTED,
                    severity=_SEVERITY_BY_CODE[
                        SemanticFindingCode.VALUE_NOT_REPRESENTED
                    ],
                    source_page_number=page_number,
                    expected_category=token.category,
                    expected_count=1,
                    observed_count=0,
                    source_excerpt=_excerpt(token.text),
                )
                for token in tokens
            )
            continue
        offsets = _page_segment_offsets(
            extraction,
            source_revision=page_claims[0].evidence.source_revision,
            page_number=page_number,
        )
        spans = {
            claim.claim_id: span
            for claim in page_claims
            if (span := _claim_span(claim, offsets)) is not None
        }
        _audit_value_claim_spans(
            page_number,
            page_claims,
            spans,
            tokens,
            findings,
        )
        represented += sum(
            any(
                claim.category is token.category
                and (span := spans.get(claim.claim_id)) is not None
                and span[0] <= token.start
                and token.end <= span[1]
                for claim in page_claims
            )
            for token in tokens
        )
        _audit_step_value_parity(
            page_number,
            page_claims,
            spans,
            tokens,
            findings,
        )
    return findings, detected, represented


def _audit_value_claim_spans(
    page_number: int,
    page_claims: Iterable[ProtocolClaim],
    spans: Mapping[str, tuple[int, int]],
    tokens: Iterable[SourceValueToken],
    findings: list[SemanticAuditFinding],
) -> None:
    """Flag value claims whose evidence fails to isolate a single value.

    Seeing two or more distinct values of the claim's own category inside its
    evidence span is decisive regardless of detector recall, so this check is
    sound in the one direction the audit reports.
    """

    for claim in page_claims:
        if claim.category not in _VALUE_CATEGORIES:
            continue
        span = spans.get(claim.claim_id)
        if span is None:
            continue
        own: dict[str, str] = {}
        for token in _tokens_within(tokens, span):
            if token.category is claim.category:
                own.setdefault(_value_key(token), token.text)
        if len(own) < 2:
            continue
        findings.append(
            SemanticAuditFinding(
                code=SemanticFindingCode.VALUE_SPAN_NOT_ISOLATED,
                severity=_SEVERITY_BY_CODE[
                    SemanticFindingCode.VALUE_SPAN_NOT_ISOLATED
                ],
                source_page_number=page_number,
                expected_category=claim.category,
                observed_category=claim.category,
                claim_id=claim.claim_id,
                step_id=claim.step_id,
                expected_count=1,
                observed_count=len(own),
                source_excerpt=_excerpt(", ".join(sorted(own.values()))),
            )
        )


def _audit_step_value_parity(
    page_number: int,
    page_claims: Iterable[ProtocolClaim],
    spans: Mapping[str, tuple[int, int]],
    tokens: Iterable[SourceValueToken],
    findings: list[SemanticAuditFinding],
) -> None:
    """Compare distinct source values per step against the claims emitted."""

    claims = tuple(page_claims)
    step_spans: dict[str, list[tuple[int, int]]] = {}
    for claim in claims:
        if claim.category is not ClaimCategory.ACTION or claim.step_id is None:
            continue
        span = spans.get(claim.claim_id)
        if span is not None:
            step_spans.setdefault(claim.step_id, []).append(span)
    for step_id in sorted(step_spans):
        step_tokens: list[SourceValueToken] = []
        for span in step_spans[step_id]:
            step_tokens.extend(_tokens_within(tokens, span))
        for category in sorted(_VALUE_CATEGORIES, key=lambda item: item.value):
            expected: dict[str, str] = {}
            for token in step_tokens:
                if token.category is category:
                    expected.setdefault(_value_key(token), token.text)
            observed = sum(
                1
                for claim in claims
                if claim.category is category and claim.step_id == step_id
            )
            # Only the deficit direction is reported.  A surplus of claims
            # over detected values is indistinguishable from this module's own
            # limited recall, so claiming it would be unsound.
            if observed >= len(expected):
                continue
            code = SemanticFindingCode.VALUE_NOT_REPRESENTED
            findings.append(
                SemanticAuditFinding(
                    code=code,
                    severity=_SEVERITY_BY_CODE[code],
                    source_page_number=page_number,
                    expected_category=category,
                    step_id=step_id,
                    expected_count=len(expected),
                    observed_count=observed,
                    source_excerpt=_excerpt(", ".join(sorted(expected.values()))),
                )
            )


def _audit_cues(
    extraction: ProtocolPdfExtraction,
    page_numbers: Iterable[int],
    by_page: Mapping[int, tuple[ProtocolClaim, ...]],
) -> list[SemanticAuditFinding]:
    findings: list[SemanticAuditFinding] = []
    for page_number in page_numbers:
        page_text = extraction.pages[page_number - 1].text
        page_claims = by_page.get(page_number, ())
        for category, code, pattern in _COMPILED_CUES:
            for match in pattern.finditer(page_text):
                cue = match.group(0)
                if any(
                    claim.category is category and cue in claim.source_text
                    for claim in page_claims
                ):
                    continue
                findings.append(
                    SemanticAuditFinding(
                        code=code,
                        severity=_SEVERITY_BY_CODE[code],
                        source_page_number=page_number,
                        expected_category=category,
                        source_excerpt=_excerpt(cue),
                    )
                )
    return findings


def _bounded(
    findings: list[SemanticAuditFinding],
) -> tuple[tuple[SemanticAuditFinding, ...], bool]:
    if len(findings) <= MAX_AUDIT_FINDINGS:
        return tuple(findings), False
    return tuple(findings[:MAX_AUDIT_FINDINGS]), True


def _build_report(
    extraction: ProtocolPdfExtraction,
    claims: tuple[ProtocolClaim, ...],
    page_numbers: tuple[int, ...],
) -> SemanticAuditReport:
    by_page = _claims_by_page(claims)
    value_findings, detected, represented = _audit_value_tokens(
        extraction,
        page_numbers,
        by_page,
    )
    findings = [
        *value_findings,
        *_audit_cues(extraction, page_numbers, by_page),
    ]
    findings.sort(
        key=lambda item: (
            item.source_page_number,
            item.code.value,
            item.claim_id or "",
            item.step_id or "",
            item.source_excerpt,
        )
    )
    bounded, truncated = _bounded(findings)
    return SemanticAuditReport(
        source_sha256=extraction.sha256,
        audited_page_numbers=page_numbers,
        audited_claim_count=len(claims),
        value_tokens_detected=detected,
        value_tokens_represented=represented,
        findings=bounded,
        findings_truncated=truncated,
    )


def _validate_pages(
    extraction: ProtocolPdfExtraction,
    page_numbers: Iterable[int],
) -> tuple[int, ...]:
    ordered = tuple(sorted({int(page) for page in page_numbers}))
    if any(page < 1 or page > extraction.page_count for page in ordered):
        raise ValueError("Semantic audit page numbers fall outside the source.")
    return ordered


def audit_chunk_semantics(
    extraction: ProtocolPdfExtraction,
    analysis: ProtocolChunkClaimAnalysis,
) -> SemanticAuditReport:
    """Audit one validated chunk against the pages it claims to have covered."""

    if analysis.source_sha256 != extraction.sha256:
        raise ValueError("Semantic audit source identity does not match.")
    page_numbers = _validate_pages(
        extraction,
        (item.source_page_number for item in analysis.page_coverage),
    )
    return _build_report(extraction, analysis.claims, page_numbers)


def audit_merged_semantics(
    extraction: ProtocolPdfExtraction,
    merged: MergedProtocolClaims,
) -> SemanticAuditReport:
    """Audit a whole merged claim set against every source page."""

    if merged.source_sha256 != extraction.sha256:
        raise ValueError("Semantic audit source identity does not match.")
    page_numbers = _validate_pages(
        extraction,
        range(1, extraction.page_count + 1),
    )
    return _build_report(extraction, merged.claims, page_numbers)


_CONSTRUCT_ID_FIELDS: tuple[str, ...] = (
    "ambiguity_id",
    "branch_id",
    "parallel_id",
    "recurring_action_id",
    "repetition_id",
    "subprocedure_id",
)


def _assembled_identifiers(protocol: object) -> frozenset[str]:
    """Collect every identifier the assembled domain protocol exposes."""

    identifiers: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, str) and value:
            identifiers.add(value)

    for prerequisite in getattr(protocol, "before_start", ()):
        add(getattr(prerequisite, "prerequisite_id", None))
    for material in getattr(protocol, "materials", ()):
        add(getattr(material, "material_id", None))
        for condition in getattr(material, "conditions", ()):
            add(getattr(condition, "statement_id", None))
    for equipment in getattr(protocol, "equipment", ()):
        add(getattr(equipment, "equipment_id", None))
    for section in getattr(protocol, "sections", ()):
        for step in getattr(section, "steps", ()):
            for action in getattr(step, "sub_actions", ()):
                add(getattr(action, "action_id", None))
                for condition in getattr(action, "conditions", ()):
                    add(getattr(condition, "statement_id", None))
                for observation in getattr(action, "required_observations", ()):
                    add(getattr(observation, "observation_id", None))
                for warning in getattr(action, "warnings", ()):
                    add(getattr(warning, "statement_id", None))
                for missing in getattr(action, "missing_execution_values", ()):
                    add(getattr(missing, "value_id", None))
                timer = getattr(action, "process_timer", None)
                if timer is not None:
                    add(getattr(timer, "timer_id", None))
    for construct in getattr(protocol, "constructs", ()):
        for field in _CONSTRUCT_ID_FIELDS:
            add(getattr(construct, field, None))
    return frozenset(identifiers)


def audit_assembly_preservation(
    merged: MergedProtocolClaims,
    protocol: object,
) -> tuple[SemanticAuditFinding, ...]:
    """Report any admitted claim that the assembled protocol does not surface.

    Assembly reaches most non-action claims only through ``target_claim_id``.
    A claim that survives canonical validation but reaches no domain object is
    invisible to a reviewer and to execution, so it is an execution-critical
    omission even though nothing was fabricated.
    """

    identifiers = _assembled_identifiers(protocol)
    suffixes = tuple(identifiers)
    findings: list[SemanticAuditFinding] = []
    for claim in merged.claims:
        if claim.claim_id in identifiers:
            continue
        marker = "-" + claim.claim_id
        if any(identifier.endswith(marker) for identifier in suffixes):
            continue
        findings.append(
            SemanticAuditFinding(
                code=SemanticFindingCode.CLAIM_LOST_IN_ASSEMBLY,
                severity=_SEVERITY_BY_CODE[
                    SemanticFindingCode.CLAIM_LOST_IN_ASSEMBLY
                ],
                source_page_number=claim.evidence.source_page_number,
                observed_category=claim.category,
                claim_id=claim.claim_id,
                step_id=claim.step_id,
                source_excerpt=_excerpt(claim.source_text),
            )
        )
    return tuple(findings)
