"""Accuracy is a second measure, not a replacement for rule conformance.

Eighteen provider calls measured whether a response satisfies the rules. A
response can satisfy every rule and still describe the wrong experiment. This
scores that separately, and the two are never combined.

The reference is a hand-built structure, not ground truth, so it is itself
audited against the source and any disagreement is reported rather than
charged to a candidate.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from voice_workflow_agent import experiment_protocol as domain
from voice_workflow_agent.experiment_protocol_pdf import extract_protocol_pdf
from voice_workflow_agent.protocol_extraction_accuracy import (
    audit_reference,
    normalize,
    score_extraction,
)

IN_GEL = Path("data/runtime/candidate-a-source/in-gel-digestion.pdf")
DEV = Path("data/development_protocols")


def _protocol(steps: tuple[tuple[str, str], ...], *, page: int = 1):
    plain = lambda text: domain.SourceEvidence(page, text)
    return domain.ExperimentProtocol(
        "protocol-x",
        domain.ProtocolMetadata(
            None, "Protocol X", "en", evidence=plain("Protocol X")
        ),
        sections=(
            domain.ProtocolSection(
                "s1",
                "Section one",
                plain("Section one"),
                tuple(
                    domain.ProtocolSourceStep(
                        f"step-{label}", label, text, plain(text)
                    )
                    for label, text in steps
                ),
            ),
        ),
    )


class NormalisationTests(unittest.TestCase):
    def test_spacing_case_and_punctuation_do_not_count(self) -> None:
        self.assertEqual(
            normalize("Incubate  for 15min, at 37°C."),
            normalize("incubate for 15min at 37 c"),
        )

    def test_micro_sign_variants_are_one_thing(self) -> None:
        self.assertEqual(normalize("500 µL"), normalize("500 μL"))

    def test_numbers_and_units_survive_normalisation(self) -> None:
        self.assertIn("500", normalize("Add 500 µL."))
        self.assertIn("15min", normalize("Wait 15min."))


class ScoringSyntheticInputTests(unittest.TestCase):
    """Task 2-4: the tool is exercised without any extraction to score."""

    REFERENCE = (
        ("1", "Cut the band into small pieces."),
        ("2", "Wash with 500 uL of solution A for 15min at 37 C."),
        ("3", "Discard the solution."),
    )

    def test_an_identical_candidate_scores_perfectly(self) -> None:
        report = score_extraction(
            _protocol(self.REFERENCE), _protocol(self.REFERENCE)
        )
        self.assertTrue(report.step_count_matches)
        self.assertTrue(report.order_matches)
        self.assertEqual(report.missing_labels, ())
        self.assertEqual(report.extra_labels, ())
        self.assertEqual(report.mean_text_similarity, 1.0)
        self.assertEqual(report.steps_with_matching_values, 3)

    def test_a_missing_step_is_named(self) -> None:
        report = score_extraction(
            _protocol(self.REFERENCE), _protocol(self.REFERENCE[:2])
        )
        self.assertFalse(report.step_count_matches)
        self.assertEqual(report.missing_labels, ("3",))
        self.assertEqual(report.extra_labels, ())

    def test_an_invented_step_is_named(self) -> None:
        report = score_extraction(
            _protocol(self.REFERENCE),
            _protocol(self.REFERENCE + (("4", "Invented step."),)),
        )
        self.assertEqual(report.extra_labels, ("4",))
        self.assertEqual(report.missing_labels, ())

    def test_reordered_steps_are_detected(self) -> None:
        reordered = (self.REFERENCE[1], self.REFERENCE[0], self.REFERENCE[2])
        report = score_extraction(
            _protocol(self.REFERENCE), _protocol(reordered)
        )
        self.assertTrue(report.step_count_matches)
        self.assertFalse(report.order_matches)

    def test_reworded_text_lowers_similarity_without_failing_values(self) -> None:
        reworded = (
            self.REFERENCE[0],
            ("2", "Rinse using 500 uL of solution A for 15min at 37 C."),
            self.REFERENCE[2],
        )
        report = score_extraction(
            _protocol(self.REFERENCE), _protocol(reworded)
        )
        self.assertLess(report.mean_text_similarity, 1.0)
        self.assertEqual(report.steps_with_matching_values, 3)

    def test_a_wrong_duration_is_caught_even_when_wording_matches(self) -> None:
        """The failure this measure exists for: rules pass, answer is wrong."""

        wrong = (
            self.REFERENCE[0],
            ("2", "Wash with 500 uL of solution A for 50min at 37 C."),
            self.REFERENCE[2],
        )
        report = score_extraction(_protocol(self.REFERENCE), _protocol(wrong))
        self.assertGreater(report.mean_text_similarity, 0.8)
        self.assertEqual(report.steps_with_matching_values, 2)
        wrong_step = next(
            item for item in report.compared if item.source_label == "2"
        )
        self.assertFalse(wrong_step.values_match)
        self.assertEqual(wrong_step.reference_values["durations"], ("900",))
        self.assertEqual(wrong_step.candidate_values["durations"], ("3000",))

    def test_a_wrong_temperature_is_caught(self) -> None:
        wrong = (
            self.REFERENCE[0],
            ("2", "Wash with 500 uL of solution A for 15min at 73 C."),
            self.REFERENCE[2],
        )
        report = score_extraction(_protocol(self.REFERENCE), _protocol(wrong))
        self.assertEqual(report.steps_with_matching_values, 2)

    def test_a_wrong_volume_is_caught(self) -> None:
        wrong = (
            self.REFERENCE[0],
            ("2", "Wash with 50 uL of solution A for 15min at 37 C."),
            self.REFERENCE[2],
        )
        report = score_extraction(_protocol(self.REFERENCE), _protocol(wrong))
        self.assertEqual(report.steps_with_matching_values, 2)


class TheMeasureStaysSeparateTests(unittest.TestCase):
    def test_the_report_says_so_and_carries_no_rule_verdict(self) -> None:
        payload = score_extraction(
            _protocol((("1", "Do a thing."),)),
            _protocol((("1", "Do a thing."),)),
        ).public_dict()
        self.assertEqual(payload["measure"], "extraction_accuracy")
        self.assertIn("never combined", payload["note"])
        for forbidden in ("readiness", "reason_codes", "canonical_validation"):
            self.assertNotIn(forbidden, payload)

    def test_it_reports_no_single_pass_or_fail(self) -> None:
        """Nothing here collapses to a verdict that could stand in for one."""

        payload = score_extraction(
            _protocol((("1", "Do a thing."),)),
            _protocol((("1", "Do a thing."),)),
        ).public_dict()
        self.assertNotIn("passed", payload)
        self.assertNotIn("score", payload)


class TheReferenceIsAuditedNotAssumedTests(unittest.TestCase):
    """Task 2-3. A disagreement with the source is reported, not scored."""

    @classmethod
    def setUpClass(cls) -> None:
        if not IN_GEL.exists():
            raise unittest.SkipTest(f"{IN_GEL} is not present.")
        from voice_workflow_agent.curated_protocol import (
            load_curated_protocol_fixture,
        )

        cls.extraction = extract_protocol_pdf(IN_GEL)
        cls.fixture = load_curated_protocol_fixture(
            (DEV / "candidate_a_curated_analysis.json").resolve(),
            (DEV / "candidate_a_curated_analysis.provenance.json").resolve(),
            IN_GEL.resolve(),
        )

    def test_the_in_gel_reference_agrees_with_its_source(self) -> None:
        self.assertEqual(
            audit_reference(self.fixture.draft.protocol, self.extraction), ()
        )

    def test_the_reference_carries_enough_values_to_be_useful(self) -> None:
        """Measured: 27 values across 11 of its 25 steps."""

        from voice_workflow_agent.protocol_extraction_accuracy import (
            _step_text,
            _steps,
            _values,
        )

        steps = _steps(self.fixture.draft.protocol)
        stated = sum(
            len(group)
            for step in steps
            for group in _values(_step_text(step)).values()
        )
        with_values = sum(
            1 for step in steps if any(_values(_step_text(step)).values())
        )
        self.assertEqual(len(steps), 25)
        self.assertEqual(stated, 27)
        self.assertEqual(with_values, 11)

    def test_a_reference_that_misquotes_its_page_is_reported(self) -> None:
        protocol = self.fixture.draft.protocol
        section = protocol.sections[0]
        step = section.steps[0]
        broken = replace(
            protocol,
            sections=(
                replace(
                    section,
                    steps=(
                        replace(
                            step,
                            evidence=replace(
                                step.evidence,
                                source_excerpt="text that is not in the source",
                            ),
                        ),
                    )
                    + section.steps[1:],
                ),
            ),
        )
        notes = audit_reference(broken, self.extraction)
        self.assertTrue(notes)
        self.assertIn("quoted excerpt is not on page", notes[0])

    def test_a_reference_stating_an_absent_value_is_reported(self) -> None:
        protocol = self.fixture.draft.protocol
        section = protocol.sections[0]
        step = section.steps[0]
        broken = replace(
            protocol,
            sections=(
                replace(
                    section,
                    steps=(
                        replace(
                            step,
                            instruction_source_text=(
                                step.instruction_source_text + " for 99min"
                            ),
                        ),
                    )
                    + section.steps[1:],
                ),
            ),
        )
        notes = audit_reference(broken, self.extraction)
        self.assertTrue(any("durations" in note for note in notes))

    def test_scoring_carries_the_reference_notes_through(self) -> None:
        notes = ("step 1: something disagrees",)
        report = score_extraction(
            _protocol((("1", "Do a thing."),)),
            _protocol((("1", "Do a thing."),)),
            reference_notes=notes,
        )
        self.assertEqual(report.public_dict()["reference_notes"], list(notes))


if __name__ == "__main__":
    unittest.main()
