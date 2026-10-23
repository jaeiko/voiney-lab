"""Score an extraction against a reference protocol.

Eighteen provider calls have measured one thing: whether a response satisfies
the rules. That is not whether it is right. A response can satisfy every rule
and still describe the wrong experiment -- wrong step count, steps in the wrong
order, a duration read from the neighbouring line.

This scores the second question, and reports it **separately**. An accuracy
score is never combined with, or substituted for, rule conformance: they answer
different questions and a document can pass one while failing the other.

The reference is a hand-built structure, not ground truth. ``audit_reference``
compares it against the source so that a disagreement between the answer key
and the document is reported rather than scored against the extraction.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum

from voiney_lab import experiment_protocol as domain

_CLOCK = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})")
_WORDED = re.compile(r"(?<![0-9])(\d{1,3})\s*(min|h|hr)\b", re.I)
_TEMPERATURE = re.compile(r"(?<![0-9])(\d{1,3})\s*(?:°|˚)?\s*C\b")
_VOLUME = re.compile(r"(?<![0-9])(\d+(?:[.,]\d+)?)\s*(m?L|[uµμ]L)\b", re.I)


def normalize(text: str) -> str:
    """Compare wording without punctuation, case or spacing noise.

    Two extractions of the same sentence differ in whitespace and in how a
    degree sign or a micro sign is encoded, and neither difference is an
    extraction error. Nothing here removes a number or a unit.
    """

    folded = unicodedata.normalize("NFKC", text).casefold()
    folded = folded.replace("µ", "μ")
    folded = re.sub(r"[^\w%.:/-]+", " ", folded)
    # A separator only carries meaning between digits: 0.5, 00:15:00, 2-7 and
    # w/v all matter, a trailing full stop does not.
    folded = re.sub(r"(?<![0-9])[.:/-]|[.:/-](?![0-9])", " ", folded)
    return " ".join(folded.split())


def _similarity(left: str, right: str) -> float:
    """Token overlap, symmetric, 1.0 for identical normalized text."""

    first, second = normalize(left).split(), normalize(right).split()
    if not first and not second:
        return 1.0
    if not first or not second:
        return 0.0
    shared = 0
    remaining = list(second)
    for token in first:
        if token in remaining:
            remaining.remove(token)
            shared += 1
    return 2 * shared / (len(first) + len(second))


def _values(text: str) -> dict[str, tuple[str, ...]]:
    """Durations, temperatures and volumes a piece of text states."""

    durations: set[int] = set()
    for match in _CLOCK.finditer(text):
        hours, minutes, seconds = (int(part) for part in match.groups())
        durations.add(hours * 3600 + minutes * 60 + seconds)
    for match in _WORDED.finditer(text):
        count, unit = int(match.group(1)), match.group(2).lower()
        durations.add(count * 60 if unit == "min" else count * 3600)
    return {
        "durations": tuple(sorted(str(value) for value in durations)),
        "temperatures": tuple(
            sorted({match.group(1) for match in _TEMPERATURE.finditer(text)})
        ),
        "volumes": tuple(
            sorted(
                {
                    f"{match.group(1).replace(',', '.')}"
                    f"{match.group(2).lower().replace('µ', 'u').replace('μ', 'u')}"
                    for match in _VOLUME.finditer(text)
                }
            )
        ),
    }


@dataclass(frozen=True)
class StepComparison:
    source_label: str
    text_similarity: float
    reference_values: dict[str, tuple[str, ...]]
    candidate_values: dict[str, tuple[str, ...]]
    source_key: str = ""

    @property
    def values_match(self) -> bool:
        return self.reference_values == self.candidate_values

    @property
    def value_outcome(self) -> str:
        """Whether the values disagree, agree, or cannot be judged here.

        Strict equality alone conflates two different things. A candidate that
        reads ``50min`` where the reference reads ``15min`` has got a value
        wrong. A candidate that reads ``1000ul`` on a step the reference is
        silent about may well be right -- measured on in-gel, the source pages
        carry three values the reference never states (a 20 minute duration,
        1000ul and 50ul), so silence there is a gap in the reference, not an
        error in the candidate.

        Scoring the second as a mismatch would charge a candidate for the
        reference's own incompleteness, and would move the number in the wrong
        direction as extraction improves. It is reported as unscorable
        instead, and counted separately.
        """

        if self.reference_values == self.candidate_values:
            return "matching"
        for kind, values in self.reference_values.items():
            candidate = self.candidate_values.get(kind, ())
            if any(value not in candidate for value in values):
                return "contradicted"
        return "reference_silent"


@dataclass(frozen=True)
class AccuracyReport:
    """Kept apart from rule conformance on purpose."""

    reference_steps: int
    candidate_steps: int
    order_matches: bool
    missing_labels: tuple[str, ...] = ()
    extra_labels: tuple[str, ...] = ()
    compared: tuple[StepComparison, ...] = ()
    reference_notes: tuple[str, ...] = ()

    @property
    def step_count_matches(self) -> bool:
        return self.reference_steps == self.candidate_steps

    @property
    def mean_text_similarity(self) -> float:
        if not self.compared:
            return 0.0
        return sum(item.text_similarity for item in self.compared) / len(
            self.compared
        )

    @property
    def steps_with_matching_values(self) -> int:
        return sum(1 for item in self.compared if item.values_match)

    @property
    def steps_with_contradicted_values(self) -> int:
        """Steps where a value the reference states is not in the candidate."""

        return sum(
            1 for item in self.compared if item.value_outcome == "contradicted"
        )

    @property
    def steps_unscorable_on_values(self) -> int:
        """Steps where the candidate states a value the reference does not."""

        return sum(
            1
            for item in self.compared
            if item.value_outcome == "reference_silent"
        )

    def public_dict(self) -> dict[str, object]:
        return {
            "measure": "extraction_accuracy",
            "note": (
                "Reported separately from rule conformance; the two answer "
                "different questions and are never combined."
            ),
            "reference_steps": self.reference_steps,
            "candidate_steps": self.candidate_steps,
            "step_count_matches": self.step_count_matches,
            "order_matches": self.order_matches,
            "missing_labels": list(self.missing_labels),
            "extra_labels": list(self.extra_labels),
            "mean_text_similarity": round(self.mean_text_similarity, 4),
            "steps_compared": len(self.compared),
            "steps_with_matching_values": self.steps_with_matching_values,
            "steps_with_contradicted_values": (
                self.steps_with_contradicted_values
            ),
            "steps_unscorable_on_values": self.steps_unscorable_on_values,
            "reference_notes": list(self.reference_notes),
        }


def _steps(protocol: domain.ExperimentProtocol) -> list[domain.ProtocolSourceStep]:
    return [
        step for section in protocol.sections for step in section.steps
    ]


def _step_text(step: domain.ProtocolSourceStep) -> str:
    parts = [step.instruction_source_text]
    parts.extend(action.instruction_source_text for action in step.sub_actions)
    return " ".join(part for part in parts if part)


def _step_keys(
    steps: list[domain.ProtocolSourceStep], ambiguous: set[str],
) -> list[str]:
    """Keep unique source labels; otherwise use the page and exact quote.

    Unnumbered and repeated labels never align by their position in a list:
    omitting its first entry would shift every subsequent comparison. A
    different quotation is reported on each side, not matched approximately.
    An occurrence suffix preserves duplicate records of the same passage.
    These are comparison addresses, never labels written into the Protocol.
    """

    seen: Counter[str] = Counter()
    result: list[str] = []
    for step in steps:
        label = step.source_label
        if label and label not in ambiguous:
            result.append(label)
            continue
        evidence = step.evidence
        quote = " ".join(evidence.source_excerpt.split())
        digest = hashlib.sha256(quote.encode("utf-8")).hexdigest()
        anchor = f"p{evidence.source_page_number}:{label or 'unnumbered'}:{digest}"
        seen[anchor] += 1
        result.append(f"{anchor}:{seen[anchor]}")
    return result


def score_extraction(
    reference: domain.ExperimentProtocol,
    candidate: domain.ExperimentProtocol,
    *,
    reference_notes: tuple[str, ...] = (),
) -> AccuracyReport:
    """Compare a candidate extraction with a reference structure."""

    reference_steps = _steps(reference)
    candidate_steps = _steps(candidate)
    repeated = {
        label for steps in (reference_steps, candidate_steps)
        for label, count in Counter(step.source_label for step in steps).items()
        if count > 1
    }
    reference_labels = _step_keys(reference_steps, repeated)
    candidate_labels = _step_keys(candidate_steps, repeated)
    reference_by_label = dict(zip(reference_labels, reference_steps, strict=True))
    by_label = dict(zip(candidate_labels, candidate_steps, strict=True))
    shared = [label for label in reference_labels if label in by_label]
    compared = tuple(
        StepComparison(
            source_label=reference_by_label[label].source_label,
            source_key=label,
            text_similarity=_similarity(
                _step_text(reference_by_label[label]),
                _step_text(by_label[label]),
            ),
            reference_values=_values(
                _step_text(reference_by_label[label])
            ),
            candidate_values=_values(_step_text(by_label[label])),
        )
        for label in shared
    )
    return AccuracyReport(
        reference_steps=len(reference_steps),
        candidate_steps=len(candidate_steps),
        order_matches=[
            label for label in candidate_labels if label in set(reference_labels)
        ]
        == [label for label in reference_labels if label in set(candidate_labels)],
        missing_labels=tuple(
            label for label in reference_labels if label not in by_label
        ),
        extra_labels=tuple(
            label for label in candidate_labels if label not in set(reference_labels)
        ),
        compared=compared,
        reference_notes=reference_notes,
    )


def _source_records(protocol: domain.ExperimentProtocol) -> Counter[tuple[str, int, str]]:
    """Count evidence-bearing typed records without relying on model ids.

    Direct scalar fields and exact evidence text identify a record. Child
    records are counted separately; renaming an id cannot create a difference.
    This measures source content presence, not semantic correctness or the
    correctness of dependencies between records.
    """

    counts: Counter[tuple[str, int, str]] = Counter()

    def visit(value: object, owner_evidence: domain.SourceEvidence | None = None) -> None:
        if isinstance(value, domain.SourceEvidence):
            return
        if isinstance(value, tuple):
            for item in value:
                visit(item, owner_evidence)
            return
        if not is_dataclass(value) or isinstance(value, type):
            return
        evidence = getattr(value, "evidence", None) or owner_evidence
        if isinstance(evidence, domain.SourceEvidence):
            content: dict[str, object] = {
                "quote": " ".join(evidence.source_excerpt.split()),
                "continued_page": evidence.continued_on_page_number,
                "continued_quote": " ".join((evidence.continued_excerpt or "").split()),
            }
            for item in fields(value):
                if item.name.endswith(("_id", "_ids")):
                    continue
                scalar = getattr(value, item.name)
                if isinstance(scalar, Enum):
                    scalar = scalar.value
                if scalar is None or isinstance(scalar, (str, int, float, bool)):
                    content[item.name] = scalar
            digest = hashlib.sha256(json.dumps(
                content, ensure_ascii=False, sort_keys=True, allow_nan=False,
            ).encode("utf-8")).hexdigest()
            counts[(type(value).__name__, evidence.source_page_number, digest)] += 1
        for item in fields(value):
            # The source PDF is input evidence, not a proposed protocol record.
            if item.name not in {"pdf", "evidence"}:
                visit(getattr(value, item.name), evidence)

    visit(protocol)
    return counts


def source_record_differences(
    reference: domain.ExperimentProtocol, candidate: domain.ExperimentProtocol,
) -> dict[str, object]:
    """Report omitted/added source records separately, never as a verdict."""

    left, right = _source_records(reference), _source_records(candidate)

    def differences(counts: Counter[tuple[str, int, str]]) -> list[dict[str, object]]:
        return [
            {"kind": kind, "source_page_number": page, "source_key": key, "count": count}
            for (kind, page, key), count in sorted(counts.items())
        ]

    return {
        "reference_count": sum(left.values()),
        "candidate_count": sum(right.values()),
        "in_reference_only": differences(left - right),
        "in_candidate_only": differences(right - left),
    }


def audit_reference(
    reference: domain.ExperimentProtocol,
    extraction,
) -> tuple[str, ...]:
    """Where the hand-built reference disagrees with the source.

    The reference is not ground truth, so anything it says that the document
    does not is reported here instead of being scored against a candidate.
    Checks only what can be checked mechanically: that each step's quoted
    instruction appears on the page it cites, and that every value the
    reference states for a step appears in that step's own source text.
    """

    notes: list[str] = []
    pages = {
        page.source_page_number: page.text for page in extraction.pages
    }
    steps = _steps(reference)
    for index, step in enumerate(steps):
        page = step.evidence.source_page_number
        text = pages.get(page)
        if text is None:
            notes.append(
                f"step {step.source_label}: cites page {page}, which the "
                f"source does not have"
            )
            continue
        if normalize(step.evidence.source_excerpt) not in normalize(text):
            notes.append(
                f"step {step.source_label}: quoted excerpt is not on page "
                f"{page}"
            )
        last = (
            steps[index + 1].evidence.source_page_number
            if index + 1 < len(steps)
            else extraction.page_count
        )
        span = " ".join(
            pages.get(number, "") for number in range(page, last + 1)
        )
        stated = _values(_step_text(step))
        present = _values(span)
        for kind, values in stated.items():
            unmatched = [
                value for value in values if value not in present[kind]
            ]
            if unmatched:
                notes.append(
                    f"step {step.source_label}: states {kind} {unmatched} "
                    f"that its source text does not"
                )
    return tuple(notes)
