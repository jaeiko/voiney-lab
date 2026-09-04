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

import re
import unicodedata
from dataclasses import dataclass, field

from voice_workflow_agent import experiment_protocol as domain

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
    folded = re.sub(r"[^0-9a-zμ%.:/-]+", " ", folded)
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

    @property
    def values_match(self) -> bool:
        return self.reference_values == self.candidate_values


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
            "reference_notes": list(self.reference_notes),
        }


def _steps(protocol: domain.ExperimentProtocol):
    return [
        step for section in protocol.sections for step in section.steps
    ]


def _step_text(step: domain.ProtocolSourceStep) -> str:
    parts = [step.instruction_source_text]
    parts.extend(action.instruction_source_text for action in step.sub_actions)
    return " ".join(part for part in parts if part)


def score_extraction(
    reference: domain.ExperimentProtocol,
    candidate: domain.ExperimentProtocol,
    *,
    reference_notes: tuple[str, ...] = (),
) -> AccuracyReport:
    """Compare a candidate extraction with a reference structure."""

    reference_steps = _steps(reference)
    candidate_steps = _steps(candidate)
    by_label = {step.source_label: step for step in candidate_steps}
    reference_labels = [step.source_label for step in reference_steps]
    candidate_labels = [step.source_label for step in candidate_steps]
    shared = [label for label in reference_labels if label in by_label]
    compared = tuple(
        StepComparison(
            source_label=label,
            text_similarity=_similarity(
                _step_text(next(s for s in reference_steps if s.source_label == label)),
                _step_text(by_label[label]),
            ),
            reference_values=_values(
                _step_text(next(s for s in reference_steps if s.source_label == label))
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
